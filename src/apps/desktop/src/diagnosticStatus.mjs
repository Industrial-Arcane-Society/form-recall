export function diagnosticStatusRows(readiness) {
  const dsh = readiness?.dsh || {}
  const occt = readiness?.occt || {}
  const catia = readiness?.catia || {}
  const python = readiness?.python || {}
  const cordis = readiness?.cordis || {}
  const externalMcp = readiness?.externalMcp || {}

  return [
    {
      key: 'dsh',
      label: 'DSH Runtime',
      value: dsh.installed ? '已安装并校验' : dsh.state === 'invalid' ? '校验失败' : '未安装',
      footerValue: dsh.installed ? '已安装' : dsh.state === 'invalid' ? '校验失败' : '未安装',
      ready: Boolean(dsh.installed),
    },
    {
      key: 'occt',
      label: 'OCCT 文件',
      value: occt.verified ? '校验通过（未加载）' : occt.state === 'invalid' ? '校验失败' : '未找到',
      footerValue: occt.verified ? '已校验' : occt.state === 'invalid' ? '校验失败' : '缺失',
      ready: Boolean(occt.verified),
    },
    {
      key: 'catia',
      label: 'CATIA COM',
      value: catia.registered === true ? '已注册（未探测连接）' : catia.registered === false ? '未注册' : '未检查',
      footerValue: catia.registered === true ? '已注册' : catia.registered === false ? '未注册' : '未检查',
      ready: null,
    },
    {
      key: 'external-mcp',
      label: '外部 MCP',
      value: externalMcp.state === 'not_configured' ? '未配置' : externalMcp.connected === true ? '已连接' : '未连接',
      footerValue: externalMcp.state === 'not_configured' ? '未配置' : externalMcp.connected === true ? '已连接' : '未连接',
      ready: null,
    },
    {
      key: 'cordis',
      label: 'Cordis 工具定义',
      value: Number.isInteger(cordis.schema_count) ? `${cordis.schema_count} 个 schema（非连通状态）` : '不可用',
      footerValue: Number.isInteger(cordis.schema_count) ? `${cordis.schema_count} 个` : '不可用',
      ready: Boolean(cordis.available),
    },
    {
      key: 'legacy-python',
      label: '旧兼容 Python',
      value: python.available ? '可用（可选）' : '未安装（可选）',
      footerValue: python.available ? '可用' : '缺失（可选）',
      ready: null,
    },
  ]
}

export function nativeSelftestMessage(report) {
  const lines = Array.isArray(report?.lines) ? report.lines : []
  return `${lines.join('\n')}\n\n状态：${report?.ok ? '通过' : '有项目未通过'}`
}
