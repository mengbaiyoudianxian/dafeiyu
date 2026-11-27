# Linux 外部依赖

以下命令适用于 Linux x86_64、Python 3.9+。从本目录运行，需要联网的下载步骤可提前在别处完成。核对、打补丁和配置入口均可离线运行。完整安装使用独立的空目录和普通服务账户。

## Python 包 twikit 2.3.3

在应用自己的虚拟环境中安装。离线安装时先在与目标兼容的 Python 环境准备完整 wheelhouse。

```sh
python -m pip download --dest wheelhouse twikit==2.3.3
python verify_external.py --archive twikit-wheel wheelhouse/twikit-2.3.3-py3-none-any.whl
python -m pip install --no-index --find-links wheelhouse twikit==2.3.3
python apply_twikit.py
python apply_twikit.py --check
```

`apply_twikit.py` 从当前解释器定位 site-packages，核对 wheel 内文件指纹后应用 `twikit-user.patch`。该补丁让部分用户资料字段缺失时仍可解析；脚本以参考安装的 SHA-256 校验最终字节。需要检查另一处安装时可加 `--site-packages DIRECTORY`。Twikit 是 MIT 许可，附上了发行包的许可文本。[PyPI 固定版本](https://pypi.org/project/twikit/2.3.3/)

参考安装还含有一份私有修改过的 `client/client.py`。公开安装使用上游客户端。普通 `--check` 会区分上游和参考客户端并校验用户解析器；对持有原环境的管理员，`--check --exact` 会要求两个文件逐字节匹配参考安装。源码差异会影响请求行为。

## Linux QQ 3.2.34-53644 和 NapCat 4.18.42

QQ 取自腾讯 CDN 的 Debian amd64 安装包。实际读取了整个包，主可执行文件和 `application.asar` 的 SHA-256 均与参考安装相同。官网当前展示的是更新版本。[腾讯 Linux QQ](https://im.qq.com/linuxqq/index.shtml) · [固定版本安装包](https://qqdl.gtimg.cn/qqfile/QQNT/9.9.36/beta/9ee04bef/linuxqq_3.2.34-53644_amd64.deb)

NapCat 上游的 v4.18.42 发布接口及原下载地址在核对时返回 HTTP 404。请自备可信来源的该版本 `NapCat.Shell.zip`。清单中 ZIP 指纹来自社区留档，同一留档的 `napcat.mjs` 和 `package.json` 与参考安装完全一致。其内部 package.json 的 version 为 0.0.1，版本识别使用发布标签与文件指纹。[上游仓库](https://github.com/NapNeko/NapCatQQ) · [社区留档记录](https://github.com/XingYunStar/NapCatQQ/releases/tag/v4.18.42)

先把自备 ZIP 放到当前目录，然后解包到空目录。Debian 系统可通过 `dpkg-deb -f linuxqq.deb Depends` 查看 QQ 声明的运行库，另按系统的桌面或无头显示方式准备图形环境。

```sh
curl -fL --retry 3 --range 0-187185495 -o linuxqq.deb \
  'https://qqdl.gtimg.cn/qqfile/QQNT/9.9.36/beta/9ee04bef/linuxqq_3.2.34-53644_amd64.deb'
python verify_external.py --archive qq-deb linuxqq.deb --archive napcat-zip NapCat.Shell.zip
mkdir qq-runtime
dpkg-deb -x linuxqq.deb qq-runtime
qq_root="$PWD/qq-runtime/opt/QQ"
nap_root="$qq_root/resources/app/app_launcher/napcat"
mkdir "$nap_root"
unzip -q NapCat.Shell.zip -d "$nap_root"
python verify_external.py --qq-root "$qq_root" --napcat-root "$nap_root"
python link_napcat.py --qq-root "$qq_root"
```

`link_napcat.py` 接受该版本的原始 QQ 应用清单或已连接的参考清单，创建相对路径加载器，再更新 Electron 入口。随后从部署文档指定的用户态显示环境启动 `"$qq_root/qq"`，在新环境自行授权 QQ，并配置与应用匹配的 OneBot 连接。

## 可选的离线语音识别

参考安装使用 sherpa-onnx v1.13.8 的 Linux x64 shared/no-TTS 命令行版及 2025-09-09 的 SenseVoice INT8 模型。运行库压缩包、其中三项二进制与参考安装逐项匹配；模型 SHA-256 与维护者仓库的 LFS 指纹一致，词表从固定修订下载后也得到相同指纹。[sherpa-onnx 发行版](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.8) · [上游模型发行资产](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) · [模型固定修订](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/tree/355f4d4884d8afd08aef04b9007a8556d7b463b2)

```sh
runtime=sherpa-onnx-v1.13.8-linux-x64-shared-no-tts
model=sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09
curl -fL --retry 3 -o "$runtime.tar.bz2" \
  "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/$runtime.tar.bz2"
curl -fL --retry 3 -o "$model.tar.bz2" \
  "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$model.tar.bz2"
python verify_external.py --archive sherpa-tar "$runtime.tar.bz2" --archive sensevoice-tar "$model.tar.bz2"
mkdir asr
tar -xjf "$runtime.tar.bz2" -C asr
tar -xjf "$model.tar.bz2" --strip-components=1 -C asr "$model/model.int8.onnx" "$model/tokens.txt"
install -m 0755 sherpa-onnx-offline asr/sherpa-onnx-offline
python verify_external.py --asr-root asr
```

将语音插件的可执行文件指向 `asr/sherpa-onnx-offline`，模型文件指向 `asr/model.int8.onnx`，词表指向 `asr/tokens.txt`。包装脚本可随目录迁移；实际识别程序与共享库保持上游发行布局。

`artifacts.json` 保存所核对的归档和关键文件的 SHA-256。`verify_external.py` 可检查任意组合，文件缺失或字节不同会以非零状态结束。仓库仅提供补丁、入口、清单、核对工具和安装说明。
