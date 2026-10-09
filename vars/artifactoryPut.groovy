// Upload files to Artifactory (with a SHA-1 checksum and optional properties).
//   artifactoryPut(url: 'https://artifactory.example.com', repo: 'generic-local', files: 'dist/*.tgz',
//                  dest: "orders-api/${env.BUILD_NUMBER}", props: [build: env.BUILD_NUMBER],
//                  tokenId: 'artifactory-token')

def call(Map cfg) {
    ppRepo('artifactory', 'put', cfg)
}
