#!/bin/bash
cd "$(dirname "$0")"
rm -f .git/index.lock
git add -A
git commit -m "Supprimer fichiers iOS/Xcode du repo" 2>/dev/null || echo "Rien de nouveau à committer"
git push origin main
echo ""
echo "✅ Push terminé. Tu peux fermer cette fenêtre."
read -p "Appuie sur Entrée pour fermer..."
