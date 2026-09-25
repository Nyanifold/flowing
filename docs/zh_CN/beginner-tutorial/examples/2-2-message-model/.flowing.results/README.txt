本示例为离线演示，无持久化结束形态。

运行命令（见教程第 2-2 篇篇首信息框）：
    uv run python demo_messages.py

demo_messages.py 离线构造 Message 对象并打印结构（不调用模型、不创建
Runtime），运行不产生 .flowing 状态目录。

已实跑确认（2026-09-21）：exit=0，输出与留档 demo_output.txt 逐字节一致，
工程目录内无任何状态产物。
