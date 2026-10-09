// Download one file from Nexus.
//   nexusGet(url: 'https://nexus.example.com', repo: 'raw-releases', path: 'orders-api/42/app.tgz',
//            out: 'app.tgz', credentialsId: 'nexus-ci')

def call(Map cfg) {
    ppRepo('nexus', 'get', cfg)
}
