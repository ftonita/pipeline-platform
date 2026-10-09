// Read secrets from Vault and expose them as environment variables inside the block.
//
//   vaultSecrets(url: 'https://vault.example.com', credentialsId: 'vault-approle',
//                env:   [DB_PASSWORD: 'apps/orders/prod#db_password'],
//                files: [SSH_KEY: 'ci/ssh/deploy#private_key']) {      // SSH_KEY holds a file path
//       sh 'deploy.sh'                                                 // $DB_PASSWORD, $SSH_KEY
//   }
//
// Auth: credentialsId = "Username with password" (role_id / secret_id of an AppRole),
//       or tokenId = "Secret text" holding a Vault token. url defaults to env.VAULT_ADDR.
// Secret spec: [kv2:|kv1:|raw:]mount/path#field. kv2 is the default; raw: reaches any other engine
//   (database/creds/ro#password, aws/creds/deploy#access_key).
// Values are NOT masked in the console log: never echo them. Files are deleted when the block ends.
// Wrap several stages in one block to share the secrets between them.

def call(Map cfg, Closure body) {
    String tmp = pwd(tmp: true)
    String id = UUID.randomUUID().toString()
    String out = "${tmp}/vault-${id}.env"
    String dir = "${tmp}/vault-${id}"
    List args = ['vault', 'export', '--out', out, '--dir', dir]
    for (e in (cfg.env ?: [:])) { args += ['--env', "${e.key}=${e.value}".toString()] }
    for (e in (cfg.files ?: [:])) { args += ['--file', "${e.key}=${e.value}".toString()] }
    Map penv = [VAULT_ADDR: cfg.url ?: env.VAULT_ADDR, VAULT_NAMESPACE: cfg.namespace, VAULT_AUTH_PATH: cfg.authPath]
    try {
        if (cfg.tokenId) {
            withCredentials([string(credentialsId: cfg.tokenId, variable: 'VAULT_TOKEN')]) { pp(args, penv) }
        } else if (cfg.credentialsId) {
            withCredentials([usernamePassword(credentialsId: cfg.credentialsId,
                    usernameVariable: 'VAULT_ROLE_ID', passwordVariable: 'VAULT_SECRET_ID')]) { pp(args, penv) }
        } else {
            error 'vaultSecrets: set credentialsId (AppRole) or tokenId (Vault token)'
        }
        List vars = []
        for (line in readFile(out).split('\n')) {
            if (line.trim()) { vars << line.toString() }
        }
        withEnv(vars) { body() }
    } finally {
        sh label: 'remove secret files', script: "rm -rf ${pp.quote(out)} ${pp.quote(dir)}"
    }
}
