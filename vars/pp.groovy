// Internal runner for resources/pp.py (Vault, Nexus, Artifactory). Other steps call it; you rarely need to.
//   pp(['nexus', 'get', 'raw', 'path/file'], [NEXUS_URL: 'https://nexus.example.com'])
// Needs python3 on the agent. Secrets travel in environment variables, never on the command line.

def call(List args, Map extraEnv = [:]) {
    String script = "${pwd(tmp: true)}/pp.py"
    writeFile file: script, text: libraryResource('pp.py')
    List envs = []
    for (e in extraEnv) {
        if (e.value) { envs << "${e.key}=${e.value}".toString() }
    }
    String cmd = "python3 ${quote(script)}"
    for (a in args) { cmd += ' ' + quote(a.toString()) }
    withEnv(envs) {
        sh label: "pp ${args.take(2).join(' ')}", script: cmd
    }
}

// POSIX single-quote escaping, safe for any value.
String quote(String s) {
    return "'" + s.replace("'", "'\\''") + "'"
}
