// Generic Java taint seed for code-plus / Joern (javasrc — match by short name).
// The per-example loop replaces mocqSource / mocqSink per vulnerability.

def mocqSource = {
  cpg.call.name("(?i)getParameter|getParameterValues|getParameterMap|getHeader|getHeaders|getQueryString|getCookies|getInputStream|getReader") ++
  cpg.parameter.where(_.method.annotation.name("(?i).*(RequestParam|PathVariable|RequestBody|RequestHeader|QueryParam|PathParam|FormParam).*"))
}

def mocqSink = {
  cpg.call
    .name("(?i)executeQuery|executeUpdate|execute|executeLargeUpdate|addBatch|prepareStatement|prepareCall|createQuery|createNativeQuery|exec|command|start|newInputStream|newOutputStream|readAllBytes")
    .argument
}

def mocqSanitizer = {
  cpg.call.name("(?i)setString|setInt|setLong|setObject|parseInt|parseLong|valueOf|escapeHtml|escapeSql|encodeForHTML|getCanonicalPath")
}

def findVulnerability() =
  mocqSink.reachableByFlows(mocqSource).l
