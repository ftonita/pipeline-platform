// Runs the vars/*.groovy steps outside Jenkins with a tiny mock of the pipeline DSL, so the real
// shell commands, quoting and environment handling are exercised. CPS/sandbox rules are NOT modelled.
//
//   java -cp groovy.jar groovy.ui.GroovyMain harness.groovy <repo-root> <scenario.groovy>
// Environment: HARNESS_SECRETS = JSON {credentialsId: "text" | {"username":..,"password":..} | {"file":..,"user":..}}
//              HARNESS_ENV     = JSON of the Jenkins `env` object (BUILD_NUMBER, ...)

import groovy.json.JsonSlurper

def repo = new File(args[0]).canonicalFile
def secrets = new JsonSlurper().parseText(System.getenv('HARNESS_SECRETS') ?: '{}')
def tmp = File.createTempDir('jenkins-tmp')
def envStack = []

// A callable that also forwards other method calls (pp.quote) to the library script.
class Step extends Closure {
    Script script
    Step(Script s) { super(s); script = s }
    Object doCall(Object... a) { script.invokeMethod('call', a) }
    Object methodMissing(String n, Object a) { script.invokeMethod(n, a) }
}

def binding = new Binding()
def run = { Map m ->
    def pb = new ProcessBuilder('bash', '-ec', m.script as String).redirectErrorStream(true)
    envStack.each { pb.environment().putAll(it) }
    def p = pb.start()
    def out = p.inputStream.text
    print out
    if (p.waitFor() != 0) { throw new RuntimeException("sh failed (${p.exitValue()})") }
    return out
}
binding.setVariable('env', new JsonSlurper().parseText(System.getenv('HARNESS_ENV') ?: '{}'))
binding.setVariable('sh', { Object a -> run(a instanceof Map ? a : [script: a]) })
binding.setVariable('pwd', { Map m = [:] -> m.tmp ? tmp.path : System.getProperty('user.dir') })
binding.setVariable('error', { String m -> throw new RuntimeException("error: $m") })
binding.setVariable('writeFile', { Map m -> new File(m.file as String).text = m.text })
binding.setVariable('readFile', { Object a -> new File(a instanceof Map ? a.file : a as String).text })
binding.setVariable('libraryResource', { String n -> new File(repo, "resources/$n").text })
binding.setVariable('withEnv', { List l, Closure c ->
    envStack << l.collectEntries { def i = it.indexOf('='); [(it.substring(0, i)): it.substring(i + 1)] }
    try { c() } finally { envStack.pop() }
})
binding.setVariable('usernamePassword', { Map m -> [m: m, kind: 'up'] })
binding.setVariable('string', { Map m -> [m: m, kind: 'str'] })
binding.setVariable('sshUserPrivateKey', { Map m -> [m: m, kind: 'ssh'] })
binding.setVariable('withCredentials', { List creds, Closure c ->
    def e = [:]
    creds.each { cr ->
        def s = secrets[cr.m.credentialsId]
        if (s == null) { throw new RuntimeException("no such credential: ${cr.m.credentialsId}") }
        if (cr.kind == 'up') { e[cr.m.usernameVariable] = s.username; e[cr.m.passwordVariable] = s.password }
        if (cr.kind == 'str') { e[cr.m.variable] = s }
        if (cr.kind == 'ssh') { e[cr.m.keyFileVariable] = s.file; e[cr.m.usernameVariable] = s.user }
    }
    envStack << e
    try { c() } finally { envStack.pop() }
})
def shell = new GroovyShell(binding)
new File(repo, 'vars').eachFileMatch(~/.*\.groovy/) { f ->
    binding.setVariable(f.name - '.groovy', new Step(shell.parse(f)))
}
shell.evaluate(new File(args[1]))
