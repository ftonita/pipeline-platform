// Upload files to a Nexus raw or maven repository.
//   nexusPut(url: 'https://nexus.example.com', repo: 'raw-releases', files: 'dist/*.tgz',
//            dest: "orders-api/${env.BUILD_NUMBER}", credentialsId: 'nexus-ci')
// credentialsId = "Username with password" (a Nexus user token works as that pair). url defaults to env.NEXUS_URL.
// files: a glob or a list of globs. dest: directory inside the repository (optional).

def call(Map cfg) {
    ppRepo('nexus', 'put', cfg)
}
