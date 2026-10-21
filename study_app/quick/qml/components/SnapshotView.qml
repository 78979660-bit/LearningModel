import QtQuick

// A page owns plain JS data rather than retaining a QVariantMap wrapper.
// Reconcile on entry and read completion, including identical DTOs that have
// no notify signal. Compare before replacing so drafts and selections survive.
Item {
    id: root
    visible: false
    property var source: null
    property string dataProperty: ""
    property bool pageVisible: true
    property bool refreshOnNotify: false
    property var value: ({})
    readonly property bool ready: value && Object.keys(value).length > 0

    function sync(force) {
        const snapshot = source && dataProperty ? source[dataProperty] : ({})
        const serialized = JSON.stringify(snapshot || ({}))
        if (force || serialized !== JSON.stringify(value)) value = JSON.parse(serialized)
    }

    onSourceChanged: sync()
    onDataPropertyChanged: sync()
    onPageVisibleChanged: if (pageVisible) sync()
    Component.onCompleted: sync()
    Connections {
        target: root.source
        enabled: root.source !== null && root.source !== undefined
        ignoreUnknownSignals: true
        function onDataChanged() { root.sync(root.refreshOnNotify) }
        function onSnapshotChanged() { root.sync() }
        function onPlanDataChanged() { root.sync() }
        function onAssistantDataChanged() { root.sync() }
        function onKnowledgeDataChanged() { root.sync() }
        function onStateChanged() { if (!root.source.busy) root.sync() }
        function onBusyChanged() { if (!root.source.busy) root.sync() }
    }
}
