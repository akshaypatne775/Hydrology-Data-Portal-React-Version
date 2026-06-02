function ModalShell({ isOpen, className = '', cardClassName = '', cardStyle, onBackdropClick, children }) {
  if (!isOpen) return null
  return (
    <div className={className} role="presentation" onClick={onBackdropClick}>
      <div className={cardClassName} style={cardStyle} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}

export default ModalShell
