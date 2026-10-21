import QtQuick

Loader {
    id: root
    property real retainedWidth: 0
    property real retainedHeight: 0
    property bool dataReady: true
    property bool presentationReady: false
    property int settlingFrames: 0
    // Incubation and nested layouts can expose zero-sized or narrow controls.
    // Keep them in the layout, but present only after a complete polish frame.
    opacity: presentationReady ? 1 : 0
    onStatusChanged: {
        settlingFrames = 0
        if (status !== Loader.Ready) presentationReady = false
        else if (root.Window.window) root.Window.window.requestUpdate()
    }
    onDataReadyChanged: {
        settlingFrames = 0
        if (!dataReady) presentationReady = false
        if (!presentationReady && root.Window.window) root.Window.window.requestUpdate()
    }
    Connections {
        target: root.Window.window
        enabled: !root.presentationReady && root.visible && root.dataReady && root.status === Loader.Ready
        function onFrameSwapped() {
            if (!root.presentationReady && root.dataReady && root.status === Loader.Ready && root.visible
                    && root.width > 0 && root.height > 0) {
                // Snapshot reconciliation timers and nested layouts receive
                // one further frame before the first visible presentation.
                root.settlingFrames += 1
                if (root.settlingFrames >= 2) root.presentationReady = true
                else root.Window.window.requestUpdate()
            }
        }
    }
    // A retained hidden page keeps its last geometry instead of repeating
    // layout and decoration work for every frame of the sidebar animation.
    width: visible ? parent.width : retainedWidth
    height: visible ? parent.height : retainedHeight
    onWidthChanged: if (visible) retainedWidth = width
    onHeightChanged: if (visible) retainedHeight = height
    enabled: visible && presentationReady
}
