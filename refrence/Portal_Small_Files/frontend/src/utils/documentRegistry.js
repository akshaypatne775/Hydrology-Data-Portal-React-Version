export const DOCUMENT_DEFS = [
  {
    label: 'Samarpan receipt',
    docType: 'samarpan',
    boolKey: 'samarpanReceipt',
    b64Key: 'samarpanFileB64',
    fileKey: 'samarpanFile',
    accept: 'image/*,application/pdf',
  },
  {
    label: 'Field survey done',
    docType: 'survey',
    boolKey: 'fieldSurveyDone',
    b64Key: 'surveyFileB64',
    fileKey: 'surveyFile',
    accept: 'image/*,application/pdf',
  },
  {
    label: 'Owner verification',
    docType: 'owner_verification',
    boolKey: 'ownerVerification',
    b64Key: 'ownerVerifFileB64',
    fileKey: 'ownerVerifFile',
    accept: 'image/*,application/pdf',
  },
  {
    label: 'Aadhar collected',
    docType: 'aadhar',
    boolKey: 'aadharCollected',
    b64Key: 'aadharFileB64',
    fileKey: 'aadharFile',
    accept: 'image/*,application/pdf',
  },
  {
    label: 'PAN collected',
    docType: 'pan',
    boolKey: 'panCollected',
    b64Key: 'panFileB64',
    fileKey: 'panFile',
    accept: 'image/*,application/pdf',
  },
  {
    label: 'Bank details collected',
    docType: 'bank',
    boolKey: 'bankDetailsCollected',
    b64Key: 'bankFileB64',
    fileKey: 'bankFile',
    accept: 'image/*,application/pdf',
  },
]

export const PHOTO_DOC_DEF = {
  label: 'Site Photo',
  docType: 'photo',
  b64Key: 'photoB64',
  fileKey: 'photoFile',
  accept: 'image/*',
}
