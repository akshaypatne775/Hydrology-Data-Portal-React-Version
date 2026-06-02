const DB_NAME = 'field-survey-offline'
const DB_VERSION = 1
const STORE = 'pendingJobs'

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onerror = () => reject(req.error)
    req.onsuccess = () => resolve(req.result)
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE, { keyPath: 'id' })
    }
  })
}

/**
 * @param {{ surveyPayload: object, shapePayloads: object[] }} job
 * @returns {Promise<string>} id
 */
export function enqueueOfflineSurveyJob(job) {
  const id = `job_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
  const record = {
    id,
    surveyPayload: job.surveyPayload,
    shapePayloads: job.shapePayloads || [],
    createdAt: new Date().toISOString(),
  }
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite')
        const store = tx.objectStore(STORE)
        const req = store.add(record)
        req.onsuccess = () => resolve(id)
        req.onerror = () => reject(req.error)
      }),
  )
}

export function getPendingOfflineCount() {
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readonly')
        const store = tx.objectStore(STORE)
        const req = store.count()
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => reject(req.error)
      }),
  )
}

function getAllPending() {
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readonly')
        const store = tx.objectStore(STORE)
        const req = store.getAll()
        req.onsuccess = () => resolve(req.result || [])
        req.onerror = () => reject(req.error)
      }),
  )
}

function deleteById(id) {
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite')
        const store = tx.objectStore(STORE)
        const req = store.delete(id)
        req.onsuccess = () => resolve()
        req.onerror = () => reject(req.error)
      }),
  )
}

/** @param {object} surveyApi module with saveSurvey / saveShape */
export async function syncPendingOfflineJobs(surveyApi) {
  const pending = await getAllPending()
  let synced = 0
  let failed = 0
  const errors = []

  for (const row of pending) {
    try {
      await surveyApi.saveSurvey(row.surveyPayload)
      for (const shapePayload of row.shapePayloads || []) {
        await surveyApi.saveShape(shapePayload)
      }
      await deleteById(row.id)
      synced += 1
    } catch (e) {
      failed += 1
      errors.push(e?.message || String(e))
    }
  }

  return { synced, failed, errors }
}
