// Internal: shared implementation of nexusPut/nexusGet/artifactoryPut/artifactoryGet.

def call(String kind, String action, Map cfg) {
    String p = kind.toUpperCase()
    List args = [kind, action, cfg.repo]
    if (action == 'put') {
        args += (cfg.files instanceof List ? cfg.files : [cfg.files])
        if (cfg.dest) { args += ['--dest', cfg.dest] }
        for (e in (cfg.props ?: [:])) { args += ['--prop', "${e.key}=${e.value}".toString()] }
    } else {
        args << cfg.path
        if (cfg.out) { args += ['--out', cfg.out] }
    }
    Map penv = [(p + '_URL'): cfg.url ?: env."${p}_URL"]
    if (cfg.tokenId) {
        withCredentials([string(credentialsId: cfg.tokenId, variable: "${p}_TOKEN".toString())]) { pp(args, penv) }
    } else if (cfg.credentialsId) {
        withCredentials([usernamePassword(credentialsId: cfg.credentialsId,
                usernameVariable: "${p}_USER".toString(), passwordVariable: "${p}_PASSWORD".toString())]) { pp(args, penv) }
    } else {
        pp(args, penv)  // anonymous
    }
}
