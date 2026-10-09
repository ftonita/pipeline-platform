// Apply THE ROLE IN THIS WORKSPACE (a role repository) to the hosts of an inventory.
//   ansibleRole(role: 'motd', inventory: 'tests/inventory', hosts: 'all', sshKeyId: 'deploy-ssh')
//   ansibleRole(role: 'motd', inventory: 'tests/inventory', check: true)             // dry run
// role = the name your playbooks use for it. hosts defaults to all. Other options as in ansibleRun
// (args for role variables, e.g. args: ['-e', '@tests/vars.yml']).

def call(Map cfg) {
    if (!cfg.role) { error 'ansibleRole: role is required' }
    String tmp = pwd(tmp: true)
    String roles = "${tmp}/ansible-roles"
    String play = "${tmp}/ansible-role-play.yml"
    sh label: 'link role', script: "mkdir -p ${pp.quote(roles)} && ln -sfn \"\$PWD\" ${pp.quote(roles + '/' + cfg.role)}"
    writeFile file: play, text: "- hosts: ${cfg.hosts ?: 'all'}\n  roles: [${cfg.role}]\n"
    String existing = env.ANSIBLE_ROLES_PATH ? ":${env.ANSIBLE_ROLES_PATH}" : ''
    Map env2 = [ANSIBLE_ROLES_PATH: "${roles}${existing}".toString()] + (cfg.env ?: [:])
    ansibleRun(cfg + [playbook: play, env: env2])
}
