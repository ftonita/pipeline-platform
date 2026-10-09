// Run a playbook.
//   ansibleRun(playbook: 'site.yml', inventory: 'inventories/prod', sshKeyId: 'deploy-ssh')
//   ansibleRun(playbook: 'site.yml', inventory: 'inventories/prod', check: true)     // --check --diff
//   ansibleRun(playbook: 'site.yml', syntax: true)                                   // --syntax-check
// Optional: args (list, e.g. ['-e', 'env=prod', '--tags', 'web']), env (map of extra environment).
// sshKeyId = "SSH Username with private key": exposed as ANSIBLE_PRIVATE_KEY_FILE / ANSIBLE_REMOTE_USER,
// which Ansible reads natively. requirements.yml in the workspace root is installed automatically.
// Needs ansible on the agent (or run inside an ansible image, see ansiblePlaybookPipeline).

def call(Map cfg = [:]) {
    String mode = cfg.syntax ? '--syntax-check' : (cfg.check ? '--check --diff' : '')
    String quoted = ''
    for (a in (cfg.args ?: [])) { quoted += ' ' + pp.quote(a.toString()) }
    List envs = [
        "PP_PLAYBOOK=${cfg.playbook ?: 'site.yml'}".toString(),
        "PP_INVENTORY=${cfg.syntax ? 'localhost,' : (cfg.inventory ?: 'inventory')}".toString(),
        "PP_MODE=${mode}".toString(),
        "PP_ARGS=${quoted}".toString(),
        'ANSIBLE_FORCE_COLOR=true',
    ]
    for (e in (cfg.env ?: [:])) { envs << "${e.key}=${e.value}".toString() }
    Closure run = {
        withEnv(envs) {
            sh label: 'ansible-playbook', script: '''
                if [ -f ansible.cfg ]; then export ANSIBLE_CONFIG="$PWD/ansible.cfg"; fi
                if [ -f requirements.yml ]; then ansible-galaxy install -r requirements.yml; fi
                eval "set -- $PP_ARGS"
                ansible-playbook -i "$PP_INVENTORY" "$PP_PLAYBOOK" $PP_MODE "$@"
            '''
        }
    }
    if (cfg.sshKeyId) {
        withCredentials([sshUserPrivateKey(credentialsId: cfg.sshKeyId,
                keyFileVariable: 'ANSIBLE_PRIVATE_KEY_FILE', usernameVariable: 'ANSIBLE_REMOTE_USER')]) { run() }
    } else {
        run()
    }
}
