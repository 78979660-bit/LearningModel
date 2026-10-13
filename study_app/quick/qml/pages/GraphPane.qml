import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    objectName: "graphPane"
    property var graph: ({})
    property string selectedKey: ""
    property bool busy: false
    property string searchText: ""
    property string stateFilter: ""
    property real zoomFactor: 1.0
    property bool userAdjustedView: false
    signal nodeSelected(string key)
    readonly property real minimumZoom: 0.02

    readonly property var allNodes: list(graph && graph.nodes)
    readonly property var visibleNodes: filteredNodes()
    readonly property real graphWidth: Math.max(310, Number(graph && graph.width) || 310)
    readonly property real graphHeight: Math.max(112, Number(graph && graph.height) || 112)

    function list(value) {
        if (!value) return []
        if (Array.isArray(value)) return value
        if (typeof value.length === "number") return value
        return []
    }

    function filteredNodes() {
        const needle = searchText.trim().toLocaleLowerCase()
        return allNodes.filter(function(node) {
            const name = String(node.label || "").toLocaleLowerCase()
            const stateOk = stateFilter.length === 0 || String(node.state || "") === stateFilter
            return stateOk && (needle.length === 0 || name.indexOf(needle) >= 0)
        })
    }

    function displayState(value) {
        const labels = {
            unlearned: "尚未学习",
            partial: "部分覆盖",
            learned_unassessed: "已覆盖 · 待评估",
            learned_assessed: "已评估",
            archived: "已归档"
        }
        return labels[String(value)] || String(value || "未知")
    }

    function nodeFill(value) {
        const colors = {
            unlearned: "#EFE9E1",
            partial: "#F1DFD7",
            learned_unassessed: "#F1DFD7",
            learned_assessed: "#E8E8DB",
            archived: "#EAE1DC"
        }
        return colors[String(value)] || Theme.card
    }

    function percent(value) {
        if (value === undefined || value === null || value === "") return "暂无评估"
        const number = Number(value)
        return Number.isFinite(number) ? Math.round(number * 100) + "%" : "暂无评估"
    }

    readonly property var nodeBounds: boundsForNodes()
    readonly property real viewWidth: nodeBounds.width * zoomFactor + 40
    readonly property real viewHeight: nodeBounds.height * zoomFactor + 40
    readonly property real layerX: Math.max(20, (viewport.width - nodeBounds.width * zoomFactor) / 2) - nodeBounds.x * zoomFactor
    readonly property real layerY: Math.max(20, (viewport.height - nodeBounds.height * zoomFactor) / 2) - nodeBounds.y * zoomFactor

    function boundsForNodes() {
        if (!visibleNodes.length) return { x: 0, y: 0, width: 120, height: 120 }
        let left = Infinity, top = Infinity, right = 0, bottom = 0
        for (const node of visibleNodes) {
            const position = node.position || ({ x: 0, y: 0 })
            const x = Number(position.x || 0) + 53
            const y = Number(position.y || 0) + 2
            left = Math.min(left, x); top = Math.min(top, y)
            right = Math.max(right, x + 120); bottom = Math.max(bottom, y + 120)
        }
        return { x: left, y: top, width: right - left, height: bottom - top }
    }

    function fitGraph(overview) {
        userAdjustedView = !!overview
        const scale = Math.min(1.35, Math.max(1, viewport.width - 40) / nodeBounds.width,
                              Math.max(1, viewport.height - 40) / nodeBounds.height)
        // Large courses open at a readable size; the overview is an explicit action.
        zoomFactor = Math.max(overview ? minimumZoom : 0.85, scale)
        viewport.contentX = 0
        viewport.contentY = 0
    }

    function scheduleAutoFit() {
        if (!visible || userAdjustedView) return
        autoFitTimer.restart()
    }

    // Retained routes cancel geometry work while hidden.
    Timer {
        id: autoFitTimer
        interval: 1
        repeat: false
        onTriggered: {
            if (root.visible && !root.userAdjustedView && viewport.width > 0 && viewport.height > 0)
                root.fitGraph()
        }
    }

    function zoomBy(amount) {
        userAdjustedView = true
        zoomFactor = Math.max(minimumZoom, Math.min(2.0, zoomFactor + amount))
    }

    function chooseNode(key) {
        nodeSelected(String(key || ""))
    }

    function moveSelection(delta) {
        if (visibleNodes.length === 0) return
        let index = visibleNodes.findIndex(function(node) { return String(node.key) === selectedKey })
        if (index < 0) index = 0
        else index = Math.max(0, Math.min(visibleNodes.length - 1, index + delta))
        chooseNode(visibleNodes[index].key)
        const button = nodeButtons.itemAt(index)
        if (!button) return
        const position = visibleNodes[index].position || ({ x: 0, y: 0 })
        const left = (Number(position.x || 0) + 53) * zoomFactor + layerX
        const top = (Number(position.y || 0) + 2) * zoomFactor + layerY
        const right = left + button.width * zoomFactor
        const bottom = top + button.height * zoomFactor
        if (left < viewport.contentX) viewport.contentX = left
        else if (right > viewport.contentX + viewport.width)
            viewport.contentX = right - viewport.width
        if (top < viewport.contentY) viewport.contentY = top
        else if (bottom > viewport.contentY + viewport.height)
            viewport.contentY = bottom - viewport.height
        button.forceActiveFocus()
    }

    function nodeAccessibleName(node) {
        const mastery = node.masteryPoint === null || node.masteryPoint === undefined
                ? "无掌握评估" : "掌握度 " + percent(node.masteryPoint)
        return String(node.label || "章节") + "，" + displayState(node.state) +
                "，覆盖 " + String(node.coverageCount || 0) + " / " +
                String(node.topicCount || 0) + " 个知识点，" + mastery
    }

    function paintEdges(context) {
        context.clearRect(0, 0, graphWidth, graphHeight)
        context.lineWidth = 1.5
        const edges = list(graph && graph.edges)
        const nodesByKey = Object.create(null)
        for (const node of visibleNodes) nodesByKey[String(node.key)] = node
        for (let index = 0; index < edges.length; index++) {
            const edge = edges[index]
            const fromNode = nodesByKey[String(edge.from)]
            const toNode = nodesByKey[String(edge.to)]
            if (!fromNode || !toNode) continue
            const from = fromNode.position || ({ x: 0, y: 0 })
            const to = toNode.position || ({ x: 0, y: 0 })
            const centerX1 = Number(from.x || 0) + 113
            const centerY1 = Number(from.y || 0) + 62
            const centerX2 = Number(to.x || 0) + 113
            const centerY2 = Number(to.y || 0) + 62
            const deltaX = centerX2 - centerX1
            const deltaY = centerY2 - centerY1
            const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY)
            if (distance < 1) continue
            const inset = 60
            const offsetX = deltaX / distance * inset
            const offsetY = deltaY / distance * inset
            context.strokeStyle = edge.relationType === "strong_prerequisite" ? "#C87D9D" : "#A7B6AA"
            context.beginPath()
            context.moveTo(centerX1 + offsetX, centerY1 + offsetY)
            context.lineTo(centerX2 - offsetX, centerY2 - offsetY)
            context.stroke()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 0
        spacing: root.height < 700 ? 6 : 10

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 8
            rowSpacing: 6
            FormField {
                id: searchField
                objectName: "knowledgeGraphSearch"
                placeholderText: "搜索章节名称"
                accessibleLabel: "搜索知识图谱章节"
                onTextChanged: root.searchText = text
            }
            FormChoice {
                id: stateChoice
                Layout.minimumWidth: 160
                objectName: "knowledgeGraphStateFilter"
                accessibleLabel: "按学习状态筛选知识图谱"
                model: [
                    { label: "全部状态", value: "" },
                    { label: "尚未学习", value: "unlearned" },
                    { label: "部分覆盖", value: "partial" },
                    { label: "已覆盖待评估", value: "learned_unassessed" },
                    { label: "已评估", value: "learned_assessed" },
                    { label: "已归档", value: "archived" }
                ]
                textRole: "label"
                valueRole: "value"
                onActivated: root.stateFilter = currentValue
            }

        }

        Text {
            objectName: "knowledgeGraphPlaceholder"
            parent: viewport
            anchors.centerIn: parent
            width: Math.max(0, parent.width - 24)
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            visible: root.allNodes.length === 0 || root.visibleNodes.length === 0
            text: root.busy ? "读取中…" : root.allNodes.length === 0
                  ? "当前学科没有可显示的章节结构。"
                  : "当前搜索和状态筛选没有匹配章节。"
            textFormat: Text.PlainText
            color: Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: 16
        }

        PaperCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 60
            padding: 8
            paperColor: Theme.card
            Flickable {
                id: viewport
                objectName: "knowledgeGraphViewport"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 60
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.HorizontalAndVerticalFlick
                onMovementStarted: root.userAdjustedView = true
                onWidthChanged: root.scheduleAutoFit()
                onHeightChanged: root.scheduleAutoFit()
                contentWidth: Math.max(width, root.viewWidth)
                contentHeight: Math.max(height, root.viewHeight)
                ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                Item {
                    id: graphLayer
                    x: root.layerX
                    y: root.layerY
                    width: root.graphWidth
                    height: root.graphHeight
                    scale: root.zoomFactor
                    transformOrigin: Item.TopLeft

                    Canvas {
                        id: edgeCanvas
                        anchors.fill: parent
                        onPaint: root.paintEdges(getContext("2d"))
                    }
    
                    Repeater {
                        id: nodeButtons
                        model: root.visibleNodes
                        delegate: Button {
                            id: nodeButton
                            required property var modelData
                            objectName: "knowledgeGraphNode_" + String(modelData.key).replace(/[^A-Za-z0-9_]/g, "_")
                            x: (Number(modelData.position && modelData.position.x) || 0) + 53
                            y: (Number(modelData.position && modelData.position.y) || 0) + 2
                            width: 120
                            height: 120
                            padding: 7
                            focusPolicy: Qt.StrongFocus
                            Accessible.name: root.nodeAccessibleName(modelData)
                            Accessible.description: "使用方向键浏览章节，回车打开章节明细。"
                            Accessible.selected: String(modelData.key) === root.selectedKey
                            Keys.priority: Keys.BeforeItem
                            Keys.onPressed: (event) => {
                                if (event.key === Qt.Key_Left || event.key === Qt.Key_Up) {
                                    root.moveSelection(-1)
                                    event.accepted = true
                                } else if (event.key === Qt.Key_Right || event.key === Qt.Key_Down) {
                                    root.moveSelection(1)
                                    event.accepted = true
                                }
                            }
                            Keys.onReturnPressed: (event) => {
                                root.chooseNode(modelData.key)
                                event.accepted = true
                            }
                            Keys.onEnterPressed: (event) => {
                                root.chooseNode(modelData.key)
                                event.accepted = true
                            }
                            onClicked: root.chooseNode(modelData.key)
                            background: Rectangle {
                                radius: width / 2
                                color: root.nodeFill(nodeButton.modelData.state)
                                border.width: nodeButton.activeFocus ? 3 : nodeButton.modelData.key === root.selectedKey ? 2 : 1
                                border.color: nodeButton.activeFocus ? Theme.ink :
                                              nodeButton.modelData.key === root.selectedKey ? Theme.accent :
                                              nodeButton.modelData.state === "learned_assessed" ? Theme.leaf : Theme.line
                            }
                            contentItem: ColumnLayout {
                                spacing: 2
                                Text {
                                    Layout.fillWidth: true
                                    text: String(nodeButton.modelData.label || "")
                                    textFormat: Text.PlainText
                                    color: Theme.ink
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 18
                                    font.weight: Font.DemiBold
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                    wrapMode: Text.Wrap
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.displayState(nodeButton.modelData.state)
                                    textFormat: Text.PlainText
                                    color: Theme.accent
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 12
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: String(nodeButton.modelData.coverageCount || 0) + "/" +
                                          String(nodeButton.modelData.topicCount || 0) + " · " +
                                          root.percent(nodeButton.modelData.masteryPoint)
                                    textFormat: Text.PlainText
                                    color: Theme.muted
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 12
                                    horizontalAlignment: Text.AlignHCenter
                                }
                            }
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Text {
                Layout.fillWidth: true
                text: root.visibleNodes.length + " 个章节"
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionFontSize
            }
            ActionButton {
                objectName: "knowledgeGraphZoomOutButton"
                text: "−"
                Layout.preferredWidth: 48
                accessibleLabel: "缩小知识图谱"
                enabled: root.zoomFactor > root.minimumZoom
                onClicked: root.zoomBy(-0.15)
            }
            ActionButton {
                objectName: "knowledgeGraphZoomInButton"
                text: "+"
                Layout.preferredWidth: 48
                accessibleLabel: "放大知识图谱"
                enabled: root.zoomFactor < 1.99
                onClicked: root.zoomBy(0.15)
            }
            ActionButton {
                objectName: "knowledgeGraphFitButton"
                text: "查看全图"
                accessibleLabel: "将全部筛选后的章节适配到画布"
                enabled: !root.busy
                onClicked: root.fitGraph(true)
            }
            ActionButton {
                objectName: "knowledgeGraphReadableButton"
                text: "还原"
                accessibleLabel: "还原可读大小"
                onClicked: { root.userAdjustedView = false; root.fitGraph(false) }
            }
        }
    }

    onGraphChanged: {
        userAdjustedView = false
        edgeCanvas.requestPaint()
        root.scheduleAutoFit()
    }
    onVisibleNodesChanged: { edgeCanvas.requestPaint(); root.scheduleAutoFit() }
    // Zoom scales the existing scene-graph texture together with its nodes.
    // The edge coordinates do not change, so no CPU repaint is required.
    onVisibleChanged: {
        if (visible) { edgeCanvas.requestPaint(); root.scheduleAutoFit() }
        else autoFitTimer.stop()
    }
    Component.onCompleted: root.scheduleAutoFit()
}


