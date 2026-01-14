我们来处理 graph connector agent，这是非常复杂的操作，我们分步骤进行。

首先，我想要的体验是:
1) 用户点击这个drop down时，assistant 会基于 drop-down menu 是否有 prefilled options 告诉我以下信息

如果没有 prefilled options，那么表明用户还没有 set up GCA，那么需要引导用户去下载、安装、注册、鉴权。我们先处理这种情况：
a. admin 要去到 https://www.microsoft.com/en-us/download/details.aspx?id=104045 这个页面去下载 graph connector agent 最新的版本到 local PC, 该 PC 需要 run windows 操作系统
b. 下载结束后，在 local PC 运行 PowerShell
c. 分别运行3个 PowerShell 命令
c.1 “Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser”
c.2 "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope LocalMachine"
c.3 "Get-ExecutionPolicy -List"
其中 c.3 是一个检查命令，需要得到如图2 的一个输出结果。检查通过之后，可以继续安装GCA，我们稍后继续谈这一步。