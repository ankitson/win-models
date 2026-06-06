# Local Gemma Lab

Small Windows/PowerShell setup for trying Gemma 4 model variants on this machine.

Large files live outside the repo under `E:\root\models`.

## Quick Start

```powershell
just status
just serve-google-qat12
just bench gemma-4-12b-qat
```

See [BENCHMARKS.md](BENCHMARKS.md) for the local benchmark notes and current recommendation.

## Useful Endpoints

- llama.cpp server: `http://localhost:8080/v1`
- LiteRT-LM server: `http://localhost:9379/v1`

For LAN clients, replace `localhost` with this machine's LAN IP.

Current common LAN URLs on this machine:

- Wi-Fi: `http://172.16.0.200:8080/v1`
- Ethernet: `http://172.16.0.209:8080/v1`
- Tailscale: `http://<tailscale-ip>:8080/v1`

## Startup

Install startup launch for the default Google QAT 12B server:

```powershell
just startup-install
```

This first tries a Windows Scheduled Task. If Windows denies that, it falls back to a user Startup-folder shortcut.

Check or remove startup:

```powershell
just startup-status
just startup-uninstall
```

To allow other LAN machines through Windows Firewall, run this from an elevated PowerShell window:

```powershell
cd C:\Users\ankit\Documents\docs-root\projects\code\local-gemma-lab
just firewall-allow 8080
```

## Variants

- `google-qat12`: official Google Gemma 4 12B IT QAT Q4_0 GGUF with multimodal projector.
- `ggml-12b-q4km`: ggml-org Gemma 4 12B IT Q4_K_M GGUF with Q8_0 projector.
- `litert-e4b`: LiteRT-LM Gemma 4 E4B IT `.litertlm`.
- `unsloth-26b-q3km`: Unsloth Gemma 4 26B-A4B IT UD-Q3_K_M GGUF.

## Notes

Reasoning is enabled for llama.cpp recipes. Use a larger `max_tokens` budget for clients,
because reasoning tokens count against the output budget.
