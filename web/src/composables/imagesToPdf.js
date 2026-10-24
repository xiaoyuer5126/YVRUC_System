/** 几张图片拼成一个 PDF，一张图一页，全在浏览器里做，服务器只收到一份 PDF。
 *
 * 盖章立项书、结项书常是手机拍的几页照片，一格只收一份文件；让人先找工具合
 * 成 PDF 太折腾。每张图先画到画布上转成 JPEG（顺带把十几 MB 的原图压小、
 * 把手机照片的拍摄方向摆正），再按 PDF 的格式直接写出来：JPEG 原样就是 PDF
 * 认的 DCTDecode 图像，不用解码，也就不用引第三方库。
 */

// 长边最多这么多像素：A4 打印 200dpi 左右，盖章、签字看得清，单页一两百 KB
const MAX_SIDE = 2200
const QUALITY = 0.85
// 页面宽度用 A4 的 595pt，高度按图片比例，图片铺满整页不留白边
const PAGE_W = 595

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => { URL.revokeObjectURL(url); resolve(img) }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`${file.name} 不是能识别的图片`)) }
    img.src = url
  })
}

async function toJpeg(file) {
  const img = await loadImage(file)
  const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight))
  const w = Math.max(1, Math.round(img.naturalWidth * scale))
  const h = Math.max(1, Math.round(img.naturalHeight * scale))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  // 透明底的 PNG 转 JPEG 会变黑底，先铺白
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, w, h)
  ctx.drawImage(img, 0, 0, w, h)
  const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', QUALITY))
  if (!blob) throw new Error(`${file.name} 转换失败`)
  return { w, h, bytes: new Uint8Array(await blob.arrayBuffer()) }
}

/** files：按页顺序排好的图片；name：生成的文件名（不带扩展名）。返回 File。 */
export async function imagesToPdf(files, name) {
  const pages = []
  for (const f of files) pages.push(await toJpeg(f))

  const enc = new TextEncoder()
  const parts = []
  const offsets = []
  let size = 0
  const put = chunk => {
    const b = typeof chunk === 'string' ? enc.encode(chunk) : chunk
    parts.push(b)
    size += b.length
  }
  // 对象号：1 目录，2 页树，之后每页三个（页面、图像、内容流）
  const obj = (n, body) => { offsets[n] = size; put(`${n} 0 obj\n`); body(); put('\nendobj\n') }

  put('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n')
  const kids = pages.map((_p, i) => `${3 + i * 3} 0 R`).join(' ')
  obj(1, () => put('<< /Type /Catalog /Pages 2 0 R >>'))
  obj(2, () => put(`<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`))
  pages.forEach((p, i) => {
    const n = 3 + i * 3
    const pw = PAGE_W
    const ph = Math.round((PAGE_W * p.h / p.w) * 100) / 100
    obj(n, () => put(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pw} ${ph}] `
      + `/Resources << /XObject << /Im0 ${n + 1} 0 R >> >> /Contents ${n + 2} 0 R >>`))
    obj(n + 1, () => {
      put(`<< /Type /XObject /Subtype /Image /Width ${p.w} /Height ${p.h} `
        + `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.bytes.length} >>\nstream\n`)
      put(p.bytes)
      put('\nendstream')
    })
    const draw = `q ${pw} 0 0 ${ph} 0 0 cm /Im0 Do Q`
    obj(n + 2, () => put(`<< /Length ${draw.length} >>\nstream\n${draw}\nendstream`))
  })
  const total = 3 + pages.length * 3
  const xref = size
  put(`xref\n0 ${total}\n0000000000 65535 f \n`)
  for (let n = 1; n < total; n++) put(`${String(offsets[n]).padStart(10, '0')} 00000 n \n`)
  put(`trailer\n<< /Size ${total} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`)
  return new File(parts, `${name}.pdf`, { type: 'application/pdf' })
}
