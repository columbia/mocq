// Generic JavaScript taint seed for code-plus / Joern.
// The per-example loop replaces mocqSource / mocqSink with vulnerability-specific
// logic; this is only the starting skeleton and baseline.

def mocqSource = {
  // request-derived input for common Node web frameworks + process/env
  cpg.call.code("(?i).*\\breq(uest)?\\.(query|params|body|headers|cookies)\\b.*") ++
  cpg.call.code("(?i).*(process\\.argv|process\\.env\\.[A-Za-z_]+).*")
}

def mocqSink = {
  cpg.call
    .name("(?i)query|execute|exec|execSync|spawn|spawnSync|raw|sendFile|readFile|writeFile|send|write|end")
    .argument
}

def mocqSanitizer = {
  cpg.call.name("(?i)parseInt|escape|encodeURIComponent|basename|normalize|resolve") ++
  cpg.call.code("(?i).*(Number\\(|shell-quote|escape-html|he\\.encode).*")
}

def findVulnerability() =
  mocqSink.reachableByFlows(mocqSource).l
