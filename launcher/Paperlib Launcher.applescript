use AppleScript version "2.4"
use scripting additions

property paperlibURL : "http://127.0.0.1:8765/"
property healthURL : "http://127.0.0.1:8765/api/v1/health"
property dockerCLI : "/usr/local/bin/docker"

on run
    set appBundlePath to POSIX path of (path to me)
    set projectFolder to do shell script "/usr/bin/dirname " & quoted form of appBundlePath
    set composeFile to projectFolder & "/compose.yaml"
    set dialogResult to display dialog "Paperlib 本地论文库\n\n启动后会自动打开浏览器。停止服务不会删除论文、配置或数据库。" buttons {"取消", "停止服务", "启动并打开"} default button "启动并打开" cancel button "取消"
    set selectedAction to button returned of dialogResult
    if selectedAction is "启动并打开" then
        my startPaperlib(composeFile)
    else if selectedAction is "停止服务" then
        my stopPaperlib(composeFile)
    end if
end run

on startPaperlib(composeFile)
    try
        tell application "Docker" to launch
    end try

    set dockerReady to false
    repeat with attempt from 1 to 60
        try
            do shell script dockerCLI & " info >/dev/null 2>&1"
            set dockerReady to true
            exit repeat
        on error
            delay 1
        end try
    end repeat
    if dockerReady is false then
        display alert "Docker Desktop 尚未就绪" message "请确认 Docker Desktop 已安装并完成首次启动，然后再次双击 Paperlib。" as critical
        return
    end if

    try
        do shell script dockerCLI & " compose -f " & quoted form of composeFile & " up -d"
    on error errorMessage
        display alert "Paperlib 启动失败" message errorMessage as critical
        return
    end try

    set serviceReady to false
    repeat with attempt from 1 to 60
        try
            do shell script "/usr/bin/curl --fail --silent " & quoted form of healthURL & " >/dev/null"
            set serviceReady to true
            exit repeat
        on error
            delay 1
        end try
    end repeat
    if serviceReady then
        open location paperlibURL
    else
        display alert "服务仍在启动" message "首次运行可能需要下载组件。请稍等一分钟后再次点击“启动并打开”。" as warning
    end if
end startPaperlib

on stopPaperlib(composeFile)
    try
        do shell script dockerCLI & " compose -f " & quoted form of composeFile & " down"
        display notification "服务已停止，论文和数据库均已保留。" with title "Paperlib"
    on error errorMessage
        display alert "停止服务失败" message errorMessage as critical
    end try
end stopPaperlib
