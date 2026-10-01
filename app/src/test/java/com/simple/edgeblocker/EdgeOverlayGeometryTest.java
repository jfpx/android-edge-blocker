package com.simple.edgeblocker;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class EdgeOverlayGeometryTest {

    @Test
    public void keepsFullTopAndBottomWhenCenterSpaceFits() {
        EdgeOverlayGeometry.Geometry geometry = EdgeOverlayGeometry.calculate(2400, 1080, true, true);

        assertEquals(400, geometry.topHeightPx);
        assertEquals(400, geometry.bottomHeightPx);
        assertEquals(280, geometry.centerHeightPx);
        assertEquals(50, geometry.leftWidthPx);
        assertEquals(50, geometry.rightWidthPx);
    }

    @Test
    public void shrinksTopAndBottomToPreserveCenterSpace() {
        EdgeOverlayGeometry.Geometry geometry = EdgeOverlayGeometry.calculate(1280, 720, true, true);

        assertEquals(260, geometry.topHeightPx);
        assertEquals(260, geometry.bottomHeightPx);
        assertEquals(200, geometry.centerHeightPx);
    }

    @Test
    public void clampsSideWidthOnTinyDisplays() {
        EdgeOverlayGeometry.Geometry geometry = EdgeOverlayGeometry.calculate(80, 1080, true, true);

        assertEquals(39, geometry.leftWidthPx);
        assertEquals(39, geometry.rightWidthPx);
    }

    @Test
    public void honorsSingleSidePreferences() {
        EdgeOverlayGeometry.Geometry geometry = EdgeOverlayGeometry.calculate(2400, 1080, false, true);

        assertFalse(geometry.hasLeftOverlay());
        assertTrue(geometry.hasRightOverlay());
        assertEquals(0, geometry.leftWidthPx);
        assertEquals(50, geometry.rightWidthPx);
    }

    @Test
    public void skipsSideOverlaysWhenNoCenterHeightRemains() {
        EdgeOverlayGeometry.Geometry geometry = EdgeOverlayGeometry.calculate(2400, 2, true, true);

        assertEquals(1, geometry.topHeightPx);
        assertEquals(1, geometry.bottomHeightPx);
        assertEquals(0, geometry.centerHeightPx);
        assertFalse(geometry.hasLeftOverlay());
        assertFalse(geometry.hasRightOverlay());
    }

    @Test
    public void handlesInvalidAndExtremeDimensionsWithoutOverflow() {
        for (int width : new int[]{Integer.MIN_VALUE, 0, 1, 2, 80, 1080, Integer.MAX_VALUE}) {
            for (int height : new int[]{Integer.MIN_VALUE, 0, 1, 2, 720, 1920, Integer.MAX_VALUE}) {
                EdgeOverlayGeometry.Geometry geometry = EdgeOverlayGeometry.calculate(
                        width, height, Integer.MAX_VALUE, Integer.MAX_VALUE, 200, true, true);
                assertTrue(geometry.topHeightPx >= 0);
                assertTrue(geometry.bottomHeightPx >= 0);
                assertTrue(geometry.centerHeightPx >= 0);
                assertEquals(geometry.screenHeightPx,
                        (long) geometry.topHeightPx + geometry.bottomHeightPx + geometry.centerHeightPx);
                assertTrue((long) geometry.leftWidthPx + geometry.rightWidthPx < geometry.screenWidthPx);
            }
        }
    }

    @Test
    public void disablesBothSidesWithoutChangingTopBottom() {
        EdgeOverlayGeometry.Geometry geometry = EdgeOverlayGeometry.calculate(1080, 1920, false, false);
        assertEquals(400, geometry.topHeightPx);
        assertEquals(400, geometry.bottomHeightPx);
        assertFalse(geometry.hasLeftOverlay());
        assertFalse(geometry.hasRightOverlay());
    }
}
