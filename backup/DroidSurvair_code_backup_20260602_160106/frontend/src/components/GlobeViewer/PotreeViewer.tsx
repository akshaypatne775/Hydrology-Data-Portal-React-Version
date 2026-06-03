type PotreeViewerProps = {
  url: string
}

export function PotreeViewer({ url }: PotreeViewerProps) {
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: '#06171b' }}>
      <iframe
        src={url}
        style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
        title="Droid 3D Point Cloud System"
      />
    </div>
  )
}

export default PotreeViewer
