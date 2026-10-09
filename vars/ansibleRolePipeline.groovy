// A complete pipeline for a repository that IS an Ansible role. Jenkinsfile:
//
//   @Library('pipeline-platform@v1') _
//   ansibleRolePipeline(role: 'motd', inventory: 'tests/inventory', sshKeyId: 'deploy-ssh')
//
// Stages: Lint, Syntax (always) | Check on pull requests, Approve + Apply on the default branch
// (only when inventory is set). Options: hosts, args, defaultBranch (main), image, lintImage.
// Stages run in Docker containers (Docker Pipeline plugin); for plain agents compose the steps yourself.

def call(Map cfg) {
    String ansibleImage = cfg.image ?: 'willhallonline/ansible:2.16-alpine-3.19'
    String lintImg = cfg.lintImage ?: 'python:3.12-slim'
    String mainBranch = cfg.defaultBranch ?: 'main'
    boolean live = cfg.inventory as boolean
    pipeline {
        agent none
        options { disableConcurrentBuilds() }
        stages {
            stage('Lint') {
                agent { docker { image lintImg } }
                steps { ansibleLint() }
            }
            stage('Syntax') {
                agent { docker { image ansibleImage } }
                steps { ansibleRole(cfg + [syntax: true]) }
            }
            stage('Check') {
                when { allOf { changeRequest(); expression { live } }; beforeAgent true }
                agent { docker { image ansibleImage } }
                steps { ansibleRole(cfg + [check: true]) }
            }
            stage('Approve') {
                when { allOf { branch mainBranch; expression { live } } }
                steps { input message: "Apply role ${cfg.role} to ${cfg.inventory}?" }
            }
            stage('Apply') {
                when { allOf { branch mainBranch; expression { live } }; beforeAgent true }
                agent { docker { image ansibleImage } }
                steps { ansibleRole(cfg) }
            }
        }
    }
}
