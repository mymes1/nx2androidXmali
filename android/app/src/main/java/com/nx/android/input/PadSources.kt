package com.nx.android.input

/**
 * 物理键的来源编码：手柄上的一个键在表里怎么写。
 *
 * - `scan:<n>`：物理扫描码（Linux 手柄规范的 304..318）。同一个键在不同手机上扫描码可能不同，但扫描码是跨厂商稳定的，
 *   所以绑定优先用它。
 * - `key:<n>`：Android 的 keyCode（BUTTON_A 那一套）。扫描码缺失时回退到它。
 * - `axis:<id>:<sign>`：模拟轴的一半（`+`/`-` 是按下的方向）：扳机与十字键帽能这样绑，摇杆的四轴不参与。
 */
object PadSources {

    /** 禁用标记：这个键在这儿，但什么都不做。 */
    const val DISABLED = "__disabled__"

    /** 物理扫描码的来源。 */
    fun scan(scanCode: Int): String = "scan:$scanCode"

    /** Android keyCode 的来源。 */
    fun key(keyCode: Int): String = "key:$keyCode"

    /** 模拟轴半轴的来源（value 的正负是按下的方向）。 */
    fun axis(axis: Int, value: Float): String = "axis:$axis:${if (value < 0f) "-" else "+"}"

    /** 这个来源是我们认得的编码吗（读表时先过这一关）。 */
    fun isKnown(source: String): Boolean {
        if (source.isEmpty()) return false
        return when {
            source.startsWith("scan:") -> source.removePrefix("scan:").toIntOrNull()?.let { it > 0 } == true
            source.startsWith("key:") -> source.removePrefix("key:").toIntOrNull()?.let { it >= 0 } == true
            source.startsWith("axis:") -> {
                val rest = source.removePrefix("axis:")   // "<axis>:<sign>"
                val sign = rest.lastOrNull() ?: return false
                if (sign != '+' && sign != '-') return false
                val axis = rest.dropLast(2).toIntOrNull() ?: return false
                isCapturableAxis(axis)
            }
            else -> false
        }
    }

    /**
     * 这个轴能绑吗：扳机与十字键帽可以，摇杆的双向轴不行（它们是模拟量，有自己的校准）。
     * Z/RZ 也在这里——但**只对单向量程的手柄**（它们在那类手柄上是扳机；见 [bindableRange]）：
     * 老约定里 Z/RZ 是右摇杆，那一类上手柄中心不会把它们当可绑的轴喂进来。
     */
    fun isCapturableAxis(axis: Int): Boolean = when (axis) {
        15, 16 -> true    // AXIS_HAT_X, AXIS_HAT_Y
        17, 18 -> true    // AXIS_LTRIGGER, AXIS_RTRIGGER
        22, 23 -> true    // AXIS_GAS, AXIS_BRAKE
        11, 14 -> true    // AXIS_Z, AXIS_RZ：只有单向（形似扳机）才算，量程在抓键处判
        else -> false
    }

    /**
     * 这个轴**这只手柄上**能绑吗：Z/RZ 只有单向（量程从 0 起，形似扳机）才可绑——那是拓扑探测
     * 失手时玩家的自救出口；双向的 Z/RZ 是右摇杆，不参与改键。其余可绑的轴不看量程。
     */
    fun bindableRange(axis: Int, min: Float): Boolean =
        isCapturableAxis(axis) && !((axis == 11 || axis == 14) && min < 0f)

    /** Android 的 axis id（MotionEvent 的常量），推给宿主时用。 */
    const val AXIS_HAT_X = 15
    const val AXIS_HAT_Y = 16
    const val AXIS_LTRIGGER = 17
    const val AXIS_RTRIGGER = 18
}
