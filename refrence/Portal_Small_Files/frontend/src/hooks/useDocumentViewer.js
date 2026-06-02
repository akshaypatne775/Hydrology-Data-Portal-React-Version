import { useState } from 'react'
import toast from 'react-hot-toast'
import { hasStoredFileRef, resolveMediaForViewer, revokeViewerUrlIfBlob } from '../utils/storedFileRef'

function useDocumentViewer() {
  const [viewingDocUrl, setViewingDocUrl] = useState(null)
  const [viewingDocType, setViewingDocType] = useState('')

  const openDocument = (ref) => {
    if (!hasStoredFileRef(ref)) {
      toast.error('No uploaded file found.')
      return false
    }
    const result = resolveMediaForViewer(ref)
    if (!result) {
      toast.error('Could not open file.')
      return false
    }
    setViewingDocUrl(result.blobUrl)
    setViewingDocType(result.contentType || '')
    return true
  }

  const closeDocument = () => {
    revokeViewerUrlIfBlob(viewingDocUrl)
    setViewingDocUrl(null)
    setViewingDocType('')
  }

  return { viewingDocUrl, viewingDocType, openDocument, closeDocument }
}

export default useDocumentViewer
