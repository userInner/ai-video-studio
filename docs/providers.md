# Provider configuration

## Sub2API-compatible gateway

Set:

```dotenv
SUB2API_BASE_URL=https://your-gateway.example/v1
SUB2API_API_KEY=replace-me
TEXT_MODEL=your-responses-model
IMAGE_MODEL=your-image-model
```

The text gateway must support the Responses API and web search. The image
gateway must return an image URL or base64 image content.

## MiniMax speech

MiniMax is the default deployment path:

```dotenv
PREFER_LOCAL_QWEN_TTS=false
MINIMAX_API_KEY=replace-me
TTS_MODEL=speech-2.8-hd
TTS_VOICE_ID=replace-with-a-system-or-cloned-voice-id
```

Use one stable `TTS_VOICE_ID` across every scene. Do not clone a person's voice
without explicit authorization.

## Local Qwen3-TTS

Set `PREFER_LOCAL_QWEN_TTS=true` and configure a VoiceDesign checkpoint, a Base
checkpoint, the dedicated Python interpreter and a persistent reference WAV.
The first run designs the narrator once; all later scenes clone that reference
through the Base model.

No model weights or provider credentials are included in this repository.
