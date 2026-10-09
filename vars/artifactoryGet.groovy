// Download one file from Artifactory.
//   artifactoryGet(url: 'https://artifactory.example.com', repo: 'generic-local',
//                  path: 'orders-api/42/app.tgz', out: 'app.tgz', tokenId: 'artifactory-token')

def call(Map cfg) {
    ppRepo('artifactory', 'get', cfg)
}
