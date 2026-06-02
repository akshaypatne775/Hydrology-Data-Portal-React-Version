export async function fileToBase64(file, { compressImage = false } = {}) {
  if (!file) return ''
  if (!compressImage || !String(file.type || '').startsWith('image/')) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result || ''))
      reader.onerror = () => reject(new Error('File read failed'))
      reader.readAsDataURL(file)
    })
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (event) => {
      const img = new Image()
      img.onload = () => {
        const canvas = document.createElement('canvas')
        const maxWidth = 900
        const scale = Math.min(1, maxWidth / img.width)
        canvas.width = Math.max(1, Math.floor(img.width * scale))
        canvas.height = Math.max(1, Math.floor(img.height * scale))
        const ctx = canvas.getContext('2d')
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
        resolve(canvas.toDataURL('image/jpeg', 0.65))
      }
      img.onerror = () => reject(new Error('Image decode failed'))
      img.src = String(event.target?.result || '')
    }
    reader.onerror = () => reject(new Error('File read failed'))
    reader.readAsDataURL(file)
  })
}

export async function encodeSurveyFileInputs(files = {}) {
  const [
    aadharFileB64,
    panFileB64,
    bankFileB64,
    ownerVerifFileB64,
    samarpanFileB64,
    surveyFileB64,
    photoB64,
  ] = await Promise.all([
    fileToBase64(files.aadharFile),
    fileToBase64(files.panFile),
    fileToBase64(files.bankFile),
    fileToBase64(files.ownerVerifFile),
    fileToBase64(files.samarpanFile),
    fileToBase64(files.surveyFile),
    fileToBase64(files.photoFile, { compressImage: true }),
  ])

  return {
    aadharFileB64,
    panFileB64,
    bankFileB64,
    ownerVerifFileB64,
    samarpanFileB64,
    surveyFileB64,
    photoB64,
  }
}
