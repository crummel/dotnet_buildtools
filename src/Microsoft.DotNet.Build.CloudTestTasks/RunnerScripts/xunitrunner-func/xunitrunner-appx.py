#!/usr/bin/env py
import json
import os.path
import re
import shutil
import socket
import urllib
import urlparse
import sys
import helix.azure_storage
import helix.depcheck
import helix.event
import helix.logs
import helix.saferequests
import xunit
import zip_script
from helix.cmdline import command_main
from helix.io import copy_tree_to, ensure_directory_exists, fix_path

log = helix.logs.get_logger()


def _write_output_path(file_path, settings):
    (scheme,_,path,_,_,_) = urlparse.urlparse(settings.output_uri, 'file')
    if scheme.lower() == 'file':
        path = urllib.url2pathname(path)
        output_path = os.path.join(path, os.path.basename(file_path))
        shutil.copy2(file_path, output_path)
        return output_path
    else:
        try:
            fc = helix.azure_storage.get_upload_client(settings)
            url = fc.upload(file_path, os.path.basename(file_path))
            return url
        except ValueError, e:
            event_client = helix.event.create_from_uri(settings.event_uri)
            event_client.error(settings, "FailedUpload", "Failed to upload "+file_path+"after retry", None)

def _prepare_execution_environment(settings, framework_in_tpa, assembly_list_name):
    workitem_dir = fix_path(settings.workitem_working_dir)
    correlation_dir = fix_path(settings.correlation_payload_dir)


    test_drop = os.path.join(workitem_dir)

    assembly_list = os.path.join(test_drop, assembly_list_name)

    uwp_correlation_dir = os.path.join(correlation_dir, "UWPAppTools")
    uwp_runner_dir = os.path.join(correlation_dir, "UWPRunner")
    uwp_runner_working_dir = os.path.join(test_drop, "UWPRunner")
    ensure_directory_exists(uwp_runner_working_dir)
    log.info("Copying uwp binaries from {} to {}".format(uwp_correlation_dir, test_drop))
    copy_tree_to(uwp_correlation_dir, test_drop)
    log.info("Copying uwp runner from {} to {}".format(uwp_runner_dir, uwp_runner_working_dir))
    copy_tree_to(uwp_runner_dir, uwp_runner_working_dir)
    log.info("Copying product binaries from {} to {}".format(correlation_dir, test_drop))
    _copy_package_files(assembly_list, correlation_dir, test_drop)


def _copy_package_files(assembly_list, build_drop,  location):
    log.info("Opening assembly list from {}".format(assembly_list))

    try:
        tempstr = open(assembly_list).read()
        assemblylist_obj = json.loads(tempstr)

        try:
            for assembly_name in assemblylist_obj["corerun"]:
                assembly_name = assembly_name.replace("/", os.path.sep)
                assembly_name = assembly_name.replace("\\", os.path.sep)
                assembly_path = os.path.join(build_drop, assembly_name)
                target_path = os.path.join(location, os.path.basename(assembly_name))
                log.debug("Copying {} to {}".format(assembly_path, target_path))
                shutil.copy2(assembly_path, target_path)
            for assembly_name in assemblylist_obj["xunit"]:
                assembly_name = assembly_name.replace("/", os.path.sep)
                assembly_name = assembly_name.replace("\\", os.path.sep)
                assembly_path = os.path.join(build_drop, assembly_name)
                target_path = os.path.join(location, os.path.basename(assembly_name))
                log.debug("Copying {} to {}".format(assembly_path, target_path))
                shutil.copy2(assembly_path, target_path)
            for assembly_name in assemblylist_obj["testdependency"]:
                assembly_name = assembly_name.replace("/", os.path.sep)
                assembly_name = assembly_name.replace("\\", os.path.sep)
                assembly_path = os.path.join(build_drop, assembly_name)
                target_path = os.path.join(location, os.path.basename(assembly_name))
                log.debug("Copying {} to {}".format(assembly_path, target_path))
                shutil.copy2(assembly_path, target_path)
        except:
            # failed to copy a product file
            log.error("Failed to copy product binary, dumping contents of '{}'".format(build_drop))
            for root, dirs, files in os.walk(build_drop):
                for file in files:
                    log.info(os.path.join(root, file))
            # this is a fatal error so let it propagate
            raise
    except:
        #failure to find assembly list
        raise


def _run_xunit_from_execution(settings, test_dll, xunit_test_type, args):
    workitem_dir = fix_path(settings.workitem_working_dir)

    test_location = os.path.join(workitem_dir, 'UWPRunner')
    results_location = os.path.join(workitem_dir, 'test_results.xml')

    event_client = helix.event.create_from_uri(settings.event_uri)

    log.info("Starting xunit against '{}'".format(test_dll))

    env=None

    args = [os.path.join(test_location, 'xunit.console.uwp.exe'), os.path.join(workitem_dir,test_dll), '-installlocation', workitem_dir ]

    install_result =  helix.proc.run_and_log_output(
        args,
        cwd = workitem_dir,
        env = env
    )

    xunit_result = install_result

    log.info("Running of UWP app result: '{}'".format(install_result))

    #get test_results.xml
    #_copy_results_from_appdata(results_location)

    if (os.path.exists(results_location)):
        log.info("Uploading results from {}".format(results_location))

        with file(results_location) as result_file:
            test_count = 0
            for line in result_file:
                if '<assembly ' in line:
                    total_expression = re.compile(r'total="(\d+)"')
                    match = total_expression.search(line)
                    if match is not None:
                        test_count = int(match.groups()[0])
                    break

        result_url = _write_output_path(results_location, settings)
        log.info("Sending completion event")
        event_client.send(
            {
                'Type': 'XUnitTestResult',
                'WorkItemId': settings.workitem_id,
                'WorkItemFriendlyName': settings.workitem_friendly_name,
                'CorrelationId': settings.correlation_id,
                'ResultsXmlUri': result_url,
                'TestCount': test_count,
            }
        )
    else:
        log.error("Error: No exception thrown, but XUnit results not created")
        _report_error(settings)
    return xunit_result



def _copy_results_from_appdata(results_location):
    #%APPDATA%\..\Local\Packages
    roaming_dir = os.getenv('APPDATA')
    packages_dir = os.path.join(roaming_dir, '..\Local\Packages')
    dirs = [os.path.join(packages_dir, d) for d in os.listdir(packages_dir) if os.path.isdir(os.path.join(packages_dir, d))]
    output = sorted(dirs, key=lambda x: os.path.getmtime(x), reverse=True)[:1]
    localstate_dir = os.path.join(output[0], 'LocalState')
    localresults = os.path.join(output[0], 'LocalState')
    shutil.copy2(localresults, results_location)


def _report_error(settings):
    from traceback import format_tb, format_exc
    log.error("Error running xunit {}".format(format_exc()))
    (type, value, traceback) = sys.exc_info()
    event_client = helix.event.create_from_uri(settings.event_uri)
    formatted = format_tb(traceback)
    workitem_dir = fix_path(settings.workitem_working_dir)
    error_path = os.path.join(workitem_dir, 'error.log')
    lines = ['Unhandled error: {}\n{}'.format(value, formatted)]
    with open(error_path, 'w') as f:
        f.writelines(lines)
    error_url = _write_output_path(error_path, settings)
    log.info("Sending ToF test failure event")
    event_client.send(
        {
            'Type': 'XUnitTestFailure',
            'WorkItemId': settings.workitem_id,
            'WorkItemFriendlyName': settings.workitem_friendly_name,
            'CorrelationId': settings.correlation_id,
            'ErrorLogUri': error_url,
        }
    )


def run_tests(settings, test_dll, framework_in_tpa, assembly_list, perf_runner, xunit_test_type, args):
    try:
        log.info("Running on '{}'".format(socket.gethostname()))
        _prepare_execution_environment(settings, framework_in_tpa, assembly_list)

        return _run_xunit_from_execution(settings, test_dll, xunit_test_type, args)
    except:
        _report_error(settings)
        # XUnit will now only return 0-4 for return codes.
        # so, use 5 to indicate a non-XUnit failure
        return 5



def main(args=None):
    def _main(settings, optlist, args):
        """
        Usage::
            xunitrunner
                [--config config.json]
                [--setting name=value]
                --dll Test.dll
        """
        optdict = dict(optlist)
        # check if a perf runner has been specified
        perf_runner = None
        assembly_list = None
        if '--perf-runner' in optdict:
            perf_runner = optdict['--perf-runner']
        if '--assemblylist' in optdict:
            assembly_list = optdict['--assemblylist']
            log.info("Using assemblylist parameter:"+assembly_list)
        else:
            assembly_list = os.getenv('HELIX_ASSEMBLY_LIST')
            log.info("Using assemblylist environment variable:"+assembly_list)

        xunit_test_type = xunit.XUNIT_CONFIG_NETCORE
        if '--xunit-test-type' in optdict:
            xunit_test_type = optdict['--xunit-test-type']
        if os.name != 'nt' and xunit_test_type == xunit.XUNIT_CONFIG_DESKTOP:
            raise Exception("Error: Cannot run desktop xunit on non windows platforms")

        return run_tests(settings, optdict['--dll'], '--tpaframework' in optdict, assembly_list, perf_runner, xunit_test_type, args)

    return command_main(_main, ['dll=', 'tpaframework', 'perf-runner=', 'assemblylist=', 'xunit-test-type='], args)

if __name__ == '__main__':
    import sys
    sys.exit(main())

helix.depcheck.check_dependencies(__name__)
