using Microsoft.Build.Framework;
using Microsoft.Build.Utilities;
using System;
using System.IO;
using System.Text;

namespace Microsoft.DotNet.Build.Tasks
{
    public class GenerateTestExecutionScripts : Task
    {
        [Required]
        public string[] TestCommands { get; set; }

        [Required]
        public string[] TestDependencies { get; set; }

        [Required]
        public string RunnerScriptTemplate { get; set; }

        [Required]
        public string ScriptOutputPath { get; set; }

        public override bool Execute()
        {
            if (!File.Exists(RunnerScriptTemplate))
            {
                throw new FileNotFoundException($"Runner script template {RunnerScriptTemplate} was not found.");
            }

            string executionScriptTemplate = File.ReadAllText(RunnerScriptTemplate);
            Directory.CreateDirectory(Path.GetDirectoryName(ScriptOutputPath));

            Log.LogMessage($"Test Command lines = {string.Join(Environment.NewLine, TestCommands)}");
            string extension = Path.GetExtension(Path.GetFileName(ScriptOutputPath)).ToLowerInvariant();
            switch (extension)
            {
                case ".sh":
                    WriteShExecutionScript(executionScriptTemplate, ScriptOutputPath);
                    break;
                case ".cmd":
                case ".bat":
                    WriteCmdExecutionScript(executionScriptTemplate, ScriptOutputPath);
                    break;
                default:
                    throw new System.NotSupportedException($"Generating runner scripts with extension '{extension}' is not yet supported");
            }
            return true;
        }

        private void WriteShExecutionScript(string shExecutionTemplate, string outputPath)
        {
            // Build up the copy commands... 
            StringBuilder copyCommands = new StringBuilder();
            foreach (string dependency in TestDependencies)
            {
                string normalizedDependency = dependency.Replace('\\', '/');
                if (normalizedDependency.StartsWith("/"))
                {
                    normalizedDependency = normalizedDependency.Substring(1);
                }
                copyCommands.Append($"cp -l -f \"$PACKAGE_DIR/{normalizedDependency}\" \"$EXECUTION_DIR/{Path.GetFileName(dependency)}\" || exit $?\n");
            }
            shExecutionTemplate = shExecutionTemplate.Replace("[[CopyFilesCommands]]", copyCommands.ToString());

            StringBuilder testRunCommands = new StringBuilder();
            foreach (string runCommand in TestCommands)
            {
                testRunCommands.Append(runCommand.Replace("CoreRun.exe", "./corerun"));
                testRunCommands.Append("\n");
            }
            shExecutionTemplate = shExecutionTemplate.Replace("[[TestRunCommand]]", testRunCommands.ToString());
            // Just in case any Windows EOLs have made it in by here, clean any up.
            shExecutionTemplate = shExecutionTemplate.Replace("\r\n", "\n");

            using (StreamWriter sw = new StreamWriter(new FileStream(outputPath, FileMode.Create)))
            {
                sw.NewLine = "\n";
                sw.Write(shExecutionTemplate);
                sw.WriteLine();
            }
            Log.LogMessage($"Wrote .sh test execution script to {outputPath}");
        }

        private void WriteCmdExecutionScript(string cmdExecutionTemplate, string outputPath)
        {
            // Build up the copy commands... 
            StringBuilder copyCommands = new StringBuilder();
            foreach (string dependency in TestDependencies)
            {
                copyCommands.AppendLine($"call :copyandcheck \"%PACKAGE_DIR%\\{dependency}\" %EXECUTION_DIR%\\{Path.GetFileName(dependency)} || GOTO EOF");
            }

            cmdExecutionTemplate = cmdExecutionTemplate.Replace("[[CopyFilesCommands]]", copyCommands.ToString());
            cmdExecutionTemplate = cmdExecutionTemplate.Replace("[[TestRunCommand]]", string.Join("\r\n", TestCommands));

            using (StreamWriter sw = new StreamWriter(new FileStream(outputPath, FileMode.Create)))
            {
                sw.Write(cmdExecutionTemplate);
                sw.WriteLine();
            }
            Log.LogMessage($"Wrote .cmd test execution script to {outputPath}");
        }
    }
}