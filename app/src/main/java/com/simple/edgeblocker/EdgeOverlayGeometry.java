package com.simple.edgeblocker;

final class EdgeOverlayGeometry {

    static final int TOP_TARGET_PX = 300;
    static final int BOTTOM_TARGET_PX = 600;
    static final int SIDE_TARGET_PX = 50;
    private static final int MIN_CENTER_PX = 200;
    private static final int MAX_CENTER_PX = 400;

    private EdgeOverlayGeometry() {
    }

    static Geometry calculate(int screenWidthPx, int screenHeightPx, boolean leftEnabled, boolean rightEnabled) {
        return calculate(
                screenWidthPx,
                screenHeightPx,
                TOP_TARGET_PX,
                BOTTOM_TARGET_PX,
                SIDE_TARGET_PX,
                resolveMinimumCenterPx(screenHeightPx),
                leftEnabled,
                rightEnabled
        );
    }

    static Geometry calculate(
            int screenWidthPx,
            int screenHeightPx,
            int requestedTopPx,
            int requestedBottomPx,
            int requestedSidePx,
            int requestedMinCenterPx,
            boolean leftEnabled,
            boolean rightEnabled
    ) {
        int safeWidth = Math.max(1, screenWidthPx);
        int safeHeight = Math.max(1, screenHeightPx);
        int topTargetPx = Math.max(1, requestedTopPx);
        int bottomTargetPx = Math.max(1, requestedBottomPx);
        long totalTargetPx = (long) topTargetPx + bottomTargetPx;
        int minCenterPx = Math.min(Math.max(0, requestedMinCenterPx), Math.max(0, safeHeight - 2));

        int totalBlockedHeight = (int) Math.min(totalTargetPx,
                Math.max(0, safeHeight - minCenterPx));
        if (safeHeight >= 2) {
            totalBlockedHeight = Math.max(2, totalBlockedHeight);
        } else {
            totalBlockedHeight = 1;
        }
        totalBlockedHeight = Math.min(totalBlockedHeight, safeHeight);

        int topHeightPx = (int) ((long) totalBlockedHeight * topTargetPx / totalTargetPx);
        if (safeHeight >= 2) {
            topHeightPx = Math.max(1, Math.min(topHeightPx, totalBlockedHeight - 1));
        }
        int bottomHeightPx = totalBlockedHeight - topHeightPx;

        if (safeHeight >= 2) {
            if (topHeightPx == 0) {
                topHeightPx = 1;
            }
            if (bottomHeightPx == 0) {
                bottomHeightPx = 1;
            }
            if (topHeightPx + bottomHeightPx > safeHeight) {
                bottomHeightPx = Math.max(1, safeHeight - topHeightPx);
            }
        } else {
            topHeightPx = 1;
            bottomHeightPx = 0;
        }

        int centerHeightPx = Math.max(0, safeHeight - topHeightPx - bottomHeightPx);
        int sideWidthPx = Math.max(1, requestedSidePx);
        int leftWidthPx = 0;
        int rightWidthPx = 0;

        if (centerHeightPx > 0) {
            if (leftEnabled && rightEnabled) {
                int maxSideWidthPx = (safeWidth - 1) / 2;
                if (maxSideWidthPx > 0) {
                    int resolvedWidthPx = Math.min(sideWidthPx, maxSideWidthPx);
                    leftWidthPx = resolvedWidthPx;
                    rightWidthPx = resolvedWidthPx;
                }
            } else if (leftEnabled) {
                leftWidthPx = Math.min(sideWidthPx, safeWidth - 1);
            } else if (rightEnabled) {
                rightWidthPx = Math.min(sideWidthPx, safeWidth - 1);
            }
        }

        return new Geometry(
                safeWidth,
                safeHeight,
                topHeightPx,
                bottomHeightPx,
                centerHeightPx,
                leftWidthPx,
                rightWidthPx,
                minCenterPx
        );
    }

    static int resolveMinimumCenterPx(int screenHeightPx) {
        int safeHeight = Math.max(1, screenHeightPx);
        return Math.min(MAX_CENTER_PX, Math.max(MIN_CENTER_PX, safeHeight / 4));
    }

    static final class Geometry {
        final int screenWidthPx;
        final int screenHeightPx;
        final int topHeightPx;
        final int bottomHeightPx;
        final int centerHeightPx;
        final int leftWidthPx;
        final int rightWidthPx;
        final int minimumCenterPx;

        Geometry(
                int screenWidthPx,
                int screenHeightPx,
                int topHeightPx,
                int bottomHeightPx,
                int centerHeightPx,
                int leftWidthPx,
                int rightWidthPx,
                int minimumCenterPx
        ) {
            this.screenWidthPx = screenWidthPx;
            this.screenHeightPx = screenHeightPx;
            this.topHeightPx = topHeightPx;
            this.bottomHeightPx = bottomHeightPx;
            this.centerHeightPx = centerHeightPx;
            this.leftWidthPx = leftWidthPx;
            this.rightWidthPx = rightWidthPx;
            this.minimumCenterPx = minimumCenterPx;
        }

        boolean hasLeftOverlay() {
            return leftWidthPx > 0 && centerHeightPx > 0;
        }

        boolean hasRightOverlay() {
            return rightWidthPx > 0 && centerHeightPx > 0;
        }
    }
}
