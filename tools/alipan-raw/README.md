# 整盘原始镜像直传阿里云盘

在 Linux 源机器上按字节顺序读取块设备或已有镜像文件，切为明文分卷，使用个人阿里云盘开放平台上传到指定目录。默认每卷 512 MiB，同时处理最多 3 卷，每个卷按顺序上传 4 MiB 分片。末卷保留实际长度。云端确认卷大小和 SHA-1 后才删除本地暂存卷，最后上传各卷和整盘的 SHA-256 清单。

## 安装和授权

需要 Linux、Python 3.11 或更高版本、OpenSSL 3 的 `libcrypto.so.3`、Python `requests`、可用的个人阿里云盘账号和联网权限。可选后台服务使用 systemd 用户实例。Ubuntu 24.04 可用 `sudo apt install python3 python3-requests libssl3t64`。未使用 Linux 或发行版的 Python ABI 时请先运行下面的离线测试。

1. 从项目发布页下载与你的 Linux 架构匹配的 `tickstep/aliyunpan` **v0.4.0**，解压并将可执行文件放入自己的 PATH。此处使用该第三方 CLI 的正常交互授权流程和配置格式。[CLI 发布页](https://github.com/tickstep/aliyunpan/releases/tag/v0.4.0)
2. 在源机器上创建专用的私有 CLI 配置目录，然后运行下列命令。按 CLI 提示在浏览器完成阿里云盘授权及扫码，选择接收备份的网盘。授权可以在另一台设备的浏览器完成，分卷数据从源机器上传。[CLI 使用说明](https://github.com/tickstep/aliyunpan#如何使用)

   ```sh
   umask 077
   export ALIYUNPAN_CONFIG_DIR="$HOME/.config/alipan-raw-login"
   mkdir -p "$ALIYUNPAN_CONFIG_DIR"
   aliyunpan login
   aliyunpan drive
   aliyunpan ls /
   ```

3. 在本目录执行 `sh install.sh`。安装脚本复制程序和服务模板，保留已有任务配置。服务由操作者手动启动。

## 准备源和目的地

* 源必须明确指定，为当前 Linux 用户可读的块设备、只读快照或不可变的镜像文件。优先使用只读快照或已停写的磁盘。有多个物理盘时，每个盘使用独立的任务状态和云目录。
* 备份正在写入的块设备需显式传 `--allow-live-device`。此模式逐段读取不同时刻的数据，无法保证文件系统或数据库在同一时刻一致。工具本身不创建快照，不提权，不写入源设备。
* 状态目录使用独立、持久、私有的目录（权限 0700）。放在待备份磁盘以外的介质，尤其是读取在线整盘时，避免把临时分卷写回正在成像的磁盘。按 `workers × volume-mib` 为暂存卷预留容量，并为 SQLite、清单和文件系统留出余量。
* 指定独占的空云目录或尚不存在的目录。程序按路径创建缺失层级，将任务绑定到当前网盘及云目录 ID。请勿在任务中途更换账号、目录、源、卷尺寸，或手动修改云端卷及本地暂存。通常关掉用于远程操作的电脑不会影响源机器上的 systemd 服务。

## 启动、恢复与日志

首次推荐用前台命令，下面的 shell 变量由操作者填写。源、目的地、任务目录均没有默认值。

```sh
python3 -u "$HOME/.local/share/alipan-raw/backup.py" \
  --source "${SOURCE:?填写源设备或镜像文件}" \
  --destination "${DESTINATION:?填写独立的云目录}" \
  --state-dir "${JOB_STATE:?填写任务状态目录}" \
  --config-dir "${ALIYUNPAN_CONFIG_DIR:?先完成CLI授权}" \
  --cli "$(command -v aliyunpan)"
```

后台运行时，编辑 `~/.config/alipan-raw/job.env`，填写每个必填值，路径使用绝对路径。systemd 的环境文件不会展开 `~` 或 `$HOME`。只需要读取在线可写块设备时，才将 `DEVICE_OPTION` 填为 `--allow-live-device`。执行：

```sh
systemctl --user daemon-reload
systemctl --user start alipan-raw.service
journalctl --user -u alipan-raw.service -f
```

如需退出会话后继续，用户的 systemd 管理器需在登出后保持运行。可按主机的管理方式配置 lingering。如需冷启动后继续，可自行执行 `systemctl --user enable alipan-raw.service`。已完成任务再次启动会读取本地完成标记并退出。

传输失败后，systemd 等待 60 秒再试。前台运行可以重跑同一命令。程序保存已冻结的本地卷和未完成分片的上传 ID，根据网盘返回的已接收分片续传。若登录过期且 CLI 无法自动续期，在原 CLI 配置目录重新完成正规登录，并选回原网盘。源文件发生变化、原始暂存卷丢失或云目录冲突时会报错并保留已有数据。新的备份使用新的状态和云目录。

## 还原为镜像文件

将全部 `disk.raw.partNNNNNNNN`、`SHA256SUMS` 和 `IMAGE.sha256` 下载到独立空目录。还原目标为普通文件；需要与原盘容量相同的可用空间。下面的命令只会创建一个新的 `disk.raw`。

```sh
set -eu
export LC_ALL=C
test ! -e disk.raw
sha256sum -c SHA256SUMS
(set -C; awk '{print $2}' SHA256SUMS | while IFS= read -r volume; do
    case "$volume" in
      disk.raw.part[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) cat -- "$volume" || exit 1 ;;
      *) exit 1 ;;
    esac
done > disk.raw)
sha256sum -c IMAGE.sha256
```

分卷编号已补零，清单顺序即读取顺序。清单验证的是上传时捕获的字节。明文镜像可包含整盘数据及删除文件遗留的字节。CLI 配置、SQLite、暂存和清单均是每次运行的私有产物。

## 离线验证

在源码目录用 `python3 -B verify_offline.py` 运行合成测试。测试使用临时目录、确定性生成的数据和内存中的模拟网盘，可验证首次运行、中途断开、分片恢复、末卷、重复运行及校验清单；不会读取真实磁盘或访问网盘。真实授权、账号权限、可用空间及服务端可达性需由部署者在自己的环境确认。[个人云盘文件上传接口](https://www.yuque.com/aliyundrive/zpfszx/ezlzok)
