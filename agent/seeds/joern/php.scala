// Generic PHP taint seed for code-plus / Joern.
// The per-example loop replaces mocqSource / mocqSink with vulnerability-specific
// logic; this is only the starting skeleton and baseline.

def mocqSource = {
  // user-controlled input: PHP superglobals and request-reading calls
  cpg.call
    .name("<operator>.indexAccess")
    .where(_.argument(1).code("(?i)\\$_(GET|POST|REQUEST|COOKIE|FILES)\\b"))
    ++ cpg.call.name("(?i)file_get_contents|getenv|filter_input|apache_request_headers")
}

def mocqSink = {
  // dangerous string-consuming calls (SQLi / cmd / file); narrowed per example
  cpg.call
    .name("(?i)mysqli?_query|mysqli_real_query|pg_query|sqlite_query|query|exec|prepare|system|shell_exec|passthru|include|require|fopen|readfile")
    .argument
}

def mocqSanitizer = {
  cpg.call.name("(?i)mysqli?_real_escape_string|pg_escape_string|addslashes|intval|escapeshellarg|escapeshellcmd|basename|realpath|preg_match|ctype_[a-z]+|is_numeric|in_array")
}

def findVulnerability() =
  mocqSink.reachableByFlows(mocqSource).l
