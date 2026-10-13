import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import ".."

Rectangle {
    id: root
    default property alias content: body.data
    property int padding: 22
    property color paperColor: Theme.card
    property color borderColor: Theme.line
    property bool curled: false
    property bool surfaceEnabled: true
    readonly property bool windCurlEligible: visible && surfaceEnabled && !curled
                                              && width >= 280 && height >= 170 && padding >= 22

    implicitWidth: 280
    implicitHeight: body.implicitHeight + padding * 2
    radius: 1
    color: "transparent"
    border.width: 0

    // Thin sheets sit close to the surface; no elevated material-card shadow.
    Rectangle {
        z: -1
        x: 0; y: 2
        width: parent.width; height: parent.height
        radius: 2
        color: "#14796557"
    }
    Rectangle {
        z: -2
        x: 1; y: 4
        width: parent.width - 1; height: parent.height
        radius: 3
        color: "#08796557"
    }

    function paperOutline() {
        const points = [Qt.point(1, 1)]
        function inset(n) {
            return 1.4 + 0.7 * Math.sin(n * 1.71) + 0.32 * Math.sin(n * 3.13)
        }
        for (let x = 8; x < width - 2; x += 8) points.push(Qt.point(x, inset(x)))
        points.push(Qt.point(width - 1, 1))
        for (let y = 8; y < height - (curled ? 28 : 2); y += 8)
            points.push(Qt.point(width - inset(y + 37), y))
        if (curled) {
            points.push(Qt.point(width - 1, height - 28))
            for (let step = 1; step <= 8; ++step) {
                const t = step / 8, u = 1 - t
                points.push(Qt.point(u*u*(width-1) + 2*u*t*(width-11) + t*t*(width-28),
                                     u*u*(height-28) + 2*u*t*(height-13) + t*t*(height-1)))
            }
        } else points.push(Qt.point(width - 1, height - 1))
        for (let x = width - (curled ? 32 : 8); x > 2; x -= 8)
            points.push(Qt.point(x, height - inset(x + 71)))
        points.push(Qt.point(1, height - 1))
        for (let y = height - 8; y > 2; y -= 8) points.push(Qt.point(inset(y + 113), y))
        points.push(Qt.point(1, 1))
        return points
    }

    // Scene-graph geometry keeps the deckled edge without rasterizing and
    // uploading a full card-sized Canvas texture at every layout change.
    Shape {
        id: paperSurface
        visible: root.surfaceEnabled
        anchors.fill: parent
        ShapePath {
            strokeColor: Qt.alpha(root.borderColor, 0.40)
            strokeWidth: 0.7
            fillColor: Qt.alpha(root.paperColor, 0.98)
            PathPolyline { path: root.paperOutline() }
        }
    }

    // Keep the grain clipped to the paper edge while controls remain real
    // QML items with their usual focus and keyboard behavior.
    Rectangle {
        anchors.fill: parent
        anchors.margins: 1
        radius: Math.max(0, root.radius - 1)
        color: "transparent"
        clip: true

        Image {
            visible: root.surfaceEnabled
            anchors.fill: parent
            source: Qt.resolvedUrl("../assets/paper-texture.svg")
            sourceSize: Qt.size(960, 960)
            fillMode: Image.Tile
            opacity: 0.90
            smooth: true
            asynchronous: false
        }

        Rectangle {
            anchors.fill: parent
            color: "transparent"
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.alpha("#FFFFFF", 0.10) }
                GradientStop { position: 0.36; color: Qt.alpha("#FFFFFF", 0.025) }
                GradientStop { position: 0.78; color: Qt.alpha("#E7D6C9", 0.035) }
                GradientStop { position: 1; color: Qt.alpha("#E7D6C9", 0.055) }
            }
        }

        Rectangle {
            x: 1
            y: 0
            width: parent.width - 2
            height: 1
            color: Qt.alpha("#FFFFFF", 0.68)
        }
        Rectangle {
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 2
            anchors.rightMargin: 2
            height: 1
            color: Qt.alpha("#A48C77", 0.12)
        }
    }

    Shape {
        visible: root.curled && root.surfaceEnabled
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: 32; height: 32
        ShapePath {
            strokeColor: "transparent"
            fillColor: "#F5EFE8"
            startX: 1; startY: 30
            PathQuad { x: 30; y: 1; controlX: 22; controlY: 27 }
            PathLine { x: 30; y: 30 }
            PathLine { x: 1; y: 30 }
        }
        ShapePath {
            strokeColor: "transparent"
            fillGradient: LinearGradient {
                x1: 4; y1: 7; x2: 25; y2: 29
                GradientStop { position: 0; color: "#FFFCF5" }
                GradientStop { position: 0.7; color: "#EAD9C8" }
                GradientStop { position: 1; color: "#BCA798" }
            }
            startX: 1; startY: 30
            PathQuad { x: 30; y: 1; controlX: 7; controlY: 11 }
            PathQuad { x: 1; y: 30; controlX: 23; controlY: 24 }
        }
    }

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: root.padding
        spacing: 12
    }

    // Only a blank corner lifts. Keep layout, text, and the deckled outline fixed.
    Shape {
        objectName: "windPaperCorner"
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: 22; height: 22
        visible: root.windCurlEligible && Theme.paperLift > 0.01
        opacity: visible ? Theme.paperLift * 0.85 : 0
        transform: Scale {
            origin.x: 22; origin.y: 22
            xScale: 0.35 + Theme.paperLift * 0.65
            yScale: 0.35 + Theme.paperLift * 0.65
        }
        ShapePath {
            strokeColor: "transparent"
            fillColor: "#357B6454"
            startX: 0; startY: 22
            PathQuad { x: 22; y: 0; controlX: 16; controlY: 20 }
            PathLine { x: 22; y: 22 }
            PathLine { x: 0; y: 22 }
        }
        ShapePath {
            strokeColor: "#35AD9682"
            strokeWidth: 0.5
            fillGradient: LinearGradient {
                x1: 3; y1: 3; x2: 20; y2: 20
                GradientStop { position: 0; color: "#FFFDF7" }
                GradientStop { position: 0.65; color: "#F0E3D3" }
                GradientStop { position: 1; color: "#C9B4A2" }
            }
            startX: 0; startY: 22
            PathQuad { x: 22; y: 0; controlX: 3; controlY: 4 }
            PathQuad { x: 0; y: 22; controlX: 18; controlY: 19 }
        }
    }
}

