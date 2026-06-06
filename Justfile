set shell := ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]

mod plain 'src/plain-llama'
mod unsloth 'src/unsloth'
mod utils 'src/utils'

default:
    @just --list --list-submodules
