# Contributing

感谢你参与 AI Video Studio。

## 开始开发

1. 使用 `git clone --recurse-submodules` 克隆仓库。
2. 复制 `.env.example` 为 `.env`，不要提交任何真实密钥。
3. 运行 `./scripts/setup.sh` 安装依赖。
4. 修改前先运行 `./scripts/check.sh`，提交前再次运行。

## Pull Request

- 一个 PR 只解决一个清晰问题。
- 行为变化需要测试；界面变化请附截图。
- 不要提交生成视频、数据库、模型权重、缓存或密钥。
- 涉及事实调研、声音克隆和发布平台时，请说明隐私与合规影响。
- 保留上游白板引擎的许可证和归属说明。

## Commit

推荐使用简短的 Conventional Commit 风格，例如：

```text
feat: add a new storyboard layout
fix: keep cloned voice stable across scenes
docs: clarify MiniMax deployment
```
