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
TTS_MODEL=speech-2.8-hd
TTS_VOICE_ID=replace-with-a-system-or-cloned-voice-id
```

The user enters the MiniMax API URL and API key in the web production card.
The browser calls T2A directly, stores credentials only in `sessionStorage`,
and uploads generated MP3 files to the application. The API server never
receives the MiniMax key. Use HTTPS outside localhost and keep one stable
`TTS_VOICE_ID` across every scene. Do not clone a person's voice without
explicit authorization.

## Local Qwen3-TTS

Set `PREFER_LOCAL_QWEN_TTS=true` and configure a VoiceDesign checkpoint, a Base
checkpoint, the dedicated Python interpreter and a persistent reference WAV.
The first run designs the narrator once; all later scenes clone that reference
through the Base model.

No model weights or provider credentials are included in this repository.
