// Run ansible-lint on the workspace. Installs it into a throw-away venv when it is not on the PATH.
//   ansibleLint()                          // or ansibleLint(version: '26.9.0')

def call(Map cfg = [:]) {
    withEnv(["PP_VENV=${pwd(tmp: true)}/ansible-lint-venv".toString(),
             "PP_LINT_VERSION=${cfg.version ?: '26.9.0'}".toString()]) {
        sh label: 'ansible-lint', script: '''
            if ! command -v ansible-lint >/dev/null 2>&1; then
                python3 -m venv "$PP_VENV"
                "$PP_VENV/bin/pip" install --quiet "ansible-lint==$PP_LINT_VERSION"
                PATH="$PP_VENV/bin:$PATH"
            fi
            ansible-lint
        '''
    }
}
