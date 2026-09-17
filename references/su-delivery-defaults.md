# 本地 SU 默认交付显示

本安装用户在 2026-09-17 明确选择的默认偏好，适用于以后新生成的 SU 前期底图；具体任务有新要求时以新要求为准。它不是所有用户的通用审美规则，也不改 SketchUp 应用的全局模板或面板布局。

| 项目 | 默认设置 | 保存后核对 |
|---|---|---|
| 视图 | 平行投影、顶视图，完整底图适当留边 | 相机垂直向下，up 沿模型 +Y，perspective=false；保留底图坐标方向 |
| 长度 | 十进制、毫米 mm | UnitsOptions：LengthFormat=0、LengthUnit=2；同时抽查实际几何尺寸，不以显示单位代替比例换算 |
| 样式中的轮廓线（Profiles） | 关闭粗轮廓线，普通边线保留 | RenderingOptions：DrawSilhouettes=false；不误设为隐藏全部边线，不删除模型边 |
| 阴影面板“使用阳光进行明暗处理” | 开启，用于区分不同朝向的明暗面 | ShadowInfo：UseSunForAllShading=true |

“使用阳光”与“显示阴影”是两个参数。此次只要求前者；不据此强制打开 DisplayShadows，也不擅改 Light/Dark、日期、时间、位置和材质。同一平面同朝向的面不会仅因开启阳光就各自出现明暗差异，不通过改色或增加虚假高差制造差异。

用户要求看其手改设置时，优先读取明确的当前模型或实际可用 UI。磁盘文件尚未保存该修改时，说明只能核对已保存版本，再按用户清楚的口述记录偏好；不能声称已经看见其面板。试验用模型副本，不覆盖用户正在编辑的文件。

## 写入与回读

设置应在 SKP 保存前实际写入，保存后重新打开核对相机、两项样式/阳光参数和单位，另外保留正常几何验收。API 回读只证明文件设置，不宣称鼠标操作或屏幕外观已测试。已有场景可能恢复自己保存的相机/样式；有场景时检查默认打开状态与相关场景，不仅更改一次当前视图就宣称全文件一致。

原生 Ruby 生成路径可在当前任务授权的新模型上设置：

```ruby
model.rendering_options['DrawSilhouettes'] = false
model.shadow_info['UseSunForAllShading'] = true
model.options['UnitsOptions']['LengthFormat'] = 0
model.options['UnitsOptions']['LengthUnit'] = 2
```

相机使用顶视平行投影并按实际模型范围取景。毫米显示不会缩放原有几何；来源米制或英制时先按来源单位正确构造/换算，SketchUp API 内部长度依其接口约定处理。

已有 Windows C API 路径可用 [apply_su_defaults.py](../scripts/apply_su_defaults.py)。传入本机实际 SDK 路径、已保存源 SKP、外部工作目录内的新目标与报告路径；无 `--output` 时只读检查。脚本拒绝覆盖源 SKP/已有目标，回读两项设置及相机和毫米显示；不负责未知比例换算、全几何验收、场景批量改写或窗口面板操作。示例参数路径必须由当前项目替换：

```text
python "<Skill>/scripts/apply_su_defaults.py" "<source.skp>" --sdk "<SketchUpAPI.dll>" --output "<work/new-style.skp>" --report "<work/style-check.json>"
```

官方参数依据：[RenderingOptions](https://ruby.sketchup.com/Sketchup/RenderingOptions.html)、[ShadowInfo](https://ruby.sketchup.com/Sketchup/ShadowInfo.html)。本轮反例及证据界限见 [EF014](error-library/cases/EF014.md)。
