# FVNT

一个基于 Tkinter 的视觉小说实时翻译工具。

它会对你选定的屏幕区域持续截图，调用百度 OCR 识别文字，再调用百度翻译输出结果。项目还包含 OCR 文本清理、自定义词典热更新，以及 Nuitka 一键打包脚本。

## 功能特性

- 选定屏幕区域后持续监控并自动翻译
- 支持手动立即翻译当前区域
- OCR 文本自动清理，减少拉丁文本粘连和空格问题
- 支持自定义词典，新增词条后立即生效
- 提供 Nuitka `onefile` 打包脚本
- 运行时从外部 `config.json` 读取配置，便于本地维护密钥

## 运行环境

- Windows
- Python 3.10+
- 百度 OCR API
- 百度翻译 API

## 安装依赖

建议先创建虚拟环境，然后安装当前项目用到的主要依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

当前 `requirements.txt` 同时包含运行依赖和打包依赖。

## 配置

1. 复制 `config.example.json` 为 `config.json`
2. 填入你自己的百度 OCR 和百度翻译密钥
3. 按需修改 `custom_dict`

示例：

```json
{
  "baidu_ocr": {
    "api_key": "YOUR_BAIDU_OCR_API_KEY",
    "secret_key": "YOUR_BAIDU_OCR_SECRET_KEY"
  },
  "baidu_translate": {
    "api_key": "YOUR_BAIDU_TRANSLATE_API_KEY",
    "secret_key": "YOUR_BAIDU_TRANSLATE_SECRET_KEY",
    "term_ids": [
      "YOUR_TERM_ID"
    ]
  },
  "custom_dict": [
    "CharacterName",
    "PlaceName"
  ]
}
```

## 启动

```powershell
.\.venv\Scripts\Activate.ps1
python .\Translation.py
```

启动后：

1. 先选择监控区域
2. 选择源语言和目标语言
3. 点击开始监控，或直接立即翻译
4. 如有专有名词识别不稳定，可加入自定义词典

## 打包

项目自带 PowerShell 打包脚本：

```powershell
.\.venv\Scripts\Activate.ps1
.\build_nuitka.ps1
```

默认输出位置：

```text
build/nuitka/FVNT-Translator.exe
```

打包完成后，脚本会将本地 `config.json` 复制到输出目录，供 EXE 在运行时读取。

## 文件说明

- `Translation.py`：主程序入口
- `build_nuitka.ps1`：Nuitka 打包脚本
- `config.example.json`：配置模板，不包含真实密钥
- `config.json`：本地运行配置，已被 Git 忽略
- `embedded_config.py`：内嵌资源相关代码

## 安全说明

- 不要把真实的 `config.json` 提交到仓库
- 仓库当前只保留 `config.example.json` 作为模板
- 如果密钥曾经泄露到 Git 历史，应该立刻去服务提供方后台轮换

## License

本项目使用 MIT License，详见 `LICENSE`。