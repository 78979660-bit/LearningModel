import QtQuick

Item {
    id: root
    objectName: "startupPreloader"
    required property var bridge
    required property var pageLoader
    property bool requested: bridge ? bridge.preloadPages : false
    property bool finished: false
    readonly property bool running: requested && !finished
    readonly property bool ready: !running
    property int stepIndex: 0
    property bool applied: false
    property int settledTicks: 0
    readonly property var steps: [
        {route: "home", label: "首页"},
        {route: "plan", section: 0, label: "学习计划"},
        {route: "plan", section: 1, label: "学习记录"},
        {route: "plan", section: 2, label: "OJ"},
        {route: "plan", section: 3, label: "练习 PDF"},
        {route: "assistant", label: "学习助理"},
        {route: "knowledge", tab: "graph", label: "知识图谱"},
        {route: "knowledge", tab: "alerts", label: "学习预警"},
        {route: "settings", tab: "preferences", label: "偏好设置"},
        {route: "settings", tab: "subjects", label: "学科管理"},
        {route: "home", label: "准备进入"}
    ]
    readonly property string label: steps[Math.min(stepIndex, steps.length - 1)].label
    readonly property real progress: ready ? 1 : stepIndex / steps.length

    function dataIdle(route) {
        if (route === "home")
            return !bridge.busy && (!bridge.dailySummary || !bridge.dailySummary.busy)
        if (route === "plan") return !bridge.workspace || !bridge.workspace.busy
        if (route === "settings") return !bridge.settings || !bridge.settings.busy
        return !bridge.insights || !bridge.insights.busy
    }

    function advance() {
        const step = steps[stepIndex]
        // Visit behind the startup cover so existing page entry/refresh logic
        // and frame polishing run exactly as they do on an ordinary visit.
        if (bridge.currentPage !== step.route) bridge.navigate(step.route)
        const loader = pageLoader.currentLoader
        if (loader.status === Loader.Error) {
            nextStep()
            return
        }
        const page = loader.item
        if (!page) return
        if (!applied) {
            if (step.section !== undefined) page.section = step.section
            if (step.tab !== undefined) page.activeTab = step.tab
            applied = true
            settledTicks = 0
            return
        }
        if (!page.preloadReady || !dataIdle(step.route) || !loader.presentationReady) {
            settledTicks = 0
            return
        }
        if (step.route === "plan" && !page.panesPresented) return
        // Let subtab bindings, reconciliation timers and graph auto-fit polish
        // before retaining their geometry, including the last return to Home.
        if (++settledTicks < 2) return
        if (step.route === "plan" && step.section === 3) page.section = 0
        if (step.route === "knowledge" && step.tab === "alerts") page.activeTab = "graph"
        if (step.route === "settings" && step.tab === "subjects") page.activeTab = "preferences"
        nextStep()
    }

    function nextStep() {
        applied = false
        settledTicks = 0
        if (stepIndex + 1 >= steps.length) finished = true
        else stepIndex += 1
    }

    Timer {
        interval: 25
        repeat: true
        running: root.running
        onTriggered: root.advance()
    }
}
