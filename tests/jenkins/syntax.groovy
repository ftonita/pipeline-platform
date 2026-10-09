// Compile every file given on the command line (Jenkinsfiles lose their @Library line first).
def failed = 0
args.each { path ->
    def text = new File(path).readLines().findAll { !it.startsWith('@Library') }.join('\n')
    try { new GroovyShell().parse(text, new File(path).name); println "OK   $path" }
    catch (Throwable e) { failed++; println "FAIL $path: ${e.message}" }
}
System.exit(failed)
