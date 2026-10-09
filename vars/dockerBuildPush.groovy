// Build an image and push it to a Nexus or Artifactory docker registry (needs the docker CLI on the agent).
//   dockerBuildPush(registry: 'nexus.example.com:8082', image: 'shop/orders-api', credentialsId: 'nexus-ci')
// Pushes <registry>/<image>:<short git sha> (or the build number), plus :<tag> on tag builds.
// Optional: tag, dockerfile (Dockerfile), context (.), push (true; false = build only).
// Registry credentials live in a temporary DOCKER_CONFIG that is removed afterwards.

def call(Map cfg) {
    String tag = cfg.tag ?: (env.GIT_COMMIT ? env.GIT_COMMIT.take(8) : env.BUILD_NUMBER)
    String repo = "${cfg.registry}/${cfg.image}".toString()
    List refs = ["${repo}:${tag}".toString()]
    if (env.TAG_NAME) { refs << "${repo}:${env.TAG_NAME}".toString() }
    boolean push = cfg.push != false
    withEnv([
        "DOCKER_CONFIG=${pwd(tmp: true)}/docker-${UUID.randomUUID()}".toString(),
        "PP_REGISTRY=${cfg.registry}".toString(),
        "PP_REFS=${refs.join(' ')}".toString(),
        "PP_DOCKERFILE=${cfg.dockerfile ?: 'Dockerfile'}".toString(),
        "PP_CONTEXT=${cfg.context ?: '.'}".toString(),
        "PP_PUSH=${push}".toString(),
    ]) {
        withCredentials([usernamePassword(credentialsId: cfg.credentialsId,
                usernameVariable: 'REGISTRY_USER', passwordVariable: 'REGISTRY_PASSWORD')]) {
            try {
                sh label: 'docker build/push', script: '''
                    mkdir -p "$DOCKER_CONFIG"
                    printf '%s' "$REGISTRY_PASSWORD" | docker login "$PP_REGISTRY" -u "$REGISTRY_USER" --password-stdin
                    set -- ; for r in $PP_REFS; do set -- "$@" -t "$r"; done
                    docker build -f "$PP_DOCKERFILE" "$@" "$PP_CONTEXT"
                    if [ "$PP_PUSH" = true ]; then for r in $PP_REFS; do docker push "$r"; done; fi
                '''
            } finally {
                sh label: 'remove docker credentials', script: 'rm -rf "$DOCKER_CONFIG"'
            }
        }
    }
}
