import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import UploadPage from './components/UploadPage.jsx'
import RoomPage from './components/RoomPage.jsx'

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<UploadPage />} />
        <Route path="/room/:roomId" element={<RoomPage />} />
      </Routes>
    </Router>
  )
}

export default App
