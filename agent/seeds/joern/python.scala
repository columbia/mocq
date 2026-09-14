// Generic Python taint seed for code-plus / Joern.
// The per-example loop replaces mocqSource / mocqSink per vulnerability.

def mocqSource = {
  cpg.fieldAccess.code("(?i).*\\brequest\\.(args|form|values|json|data|files|headers|cookies|GET|POST)\\b.*") ++
  cpg.call.code("(?i).*(os\\.environ|os\\.getenv|sys\\.argv).*")
}

def mocqSink = {
  cpg.call
    .name("(?i)execute|executemany|executescript|raw|extra|system|popen|call|run|check_output|check_call|Popen|eval|exec|compile|open|remove|unlink")
    .argument
}

def mocqSanitizer = {
  cpg.call.name("(?i)int|float|shlex|quote|quote_plus|escape|secure_filename|abspath|realpath|isdigit")
}

def findVulnerability() =
  mocqSink.reachableByFlows(mocqSource).l
