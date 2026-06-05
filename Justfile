set shell := ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]

model_root := "E:\\root\\models"
host := "0.0.0.0"
llama_port := "8080"
litert_port := "9379"

default:
    just --list

status:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/status.ps1 -LlamaPort {{llama_port}} -LiteRtPort {{litert_port}}

stop:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/stop.ps1

download variant:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/download.ps1 -Variant "{{variant}}" -ModelRoot "{{model_root}}"

download-ggml12:
    just download ggml-12b-q4km

download-litert-e4b:
    just download litert-e4b

download-unsloth26:
    just download unsloth-26b-q3km

serve-google-qat12:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/serve-llama.ps1 -Variant google-qat12 -ModelRoot "{{model_root}}" -BindHost "{{host}}" -Port {{llama_port}} -Reasoning on

serve-google-qat12-lowvram:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/serve-llama.ps1 -Variant google-qat12 -ModelRoot "{{model_root}}" -BindHost "{{host}}" -Port {{llama_port}} -Reasoning on -GpuLayers 24 -CacheRam 0

serve-ggml12:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/serve-llama.ps1 -Variant ggml-12b-q4km -ModelRoot "{{model_root}}" -BindHost "{{host}}" -Port {{llama_port}} -Reasoning on

serve-unsloth26:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/serve-llama.ps1 -Variant unsloth-26b-q3km -ModelRoot "{{model_root}}" -BindHost "{{host}}" -Port {{llama_port}} -Reasoning on

serve-unsloth26-lowvram:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/serve-llama.ps1 -Variant unsloth-26b-q3km -ModelRoot "{{model_root}}" -BindHost "{{host}}" -Port {{llama_port}} -Reasoning on -GpuLayers 20 -CacheRam 0

serve-litert-e4b:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/serve-litert.ps1 -Variant litert-e4b -ModelRoot "{{model_root}}" -BindHost "{{host}}" -Port {{litert_port}}

bench model url="http://127.0.0.1:8080/v1" runs="2" max_tokens="512":
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/bench-openai.ps1 -BaseUrl "{{url}}" -Model "{{model}}" -Runs {{runs}} -MaxTokens {{max_tokens}}

bench-litert model="gemma-4-E4B-it,gpu" runs="2" max_tokens="512":
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/bench-openai.ps1 -BaseUrl "http://127.0.0.1:9379/v1" -Model "{{model}}" -Runs {{runs}} -MaxTokens {{max_tokens}}
