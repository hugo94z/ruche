# Publier Ruche sur winget

Ce document explique comment distribuer Ruche via **winget**, l'installateur de
paquets de Windows. Tout est déjà prêt dans le projet ; il reste à héberger
l'installateur et à ouvrir une pull request.

---

## 1. Ce qui protège vraiment (le mot de passe n'apportait rien)

Un mot de passe sur un installateur est une **illusion** : il est stocké dans le
fichier, lisible en quelques minutes, et il casse l'installation silencieuse
utilisée par winget. Les vraies protections sont :

| Moyen | Ce que ça apporte | Coût |
|---|---|---|
| **HTTPS** (GitHub Releases) | Le fichier ne peut pas être modifié en transit | Gratuit |
| **Empreinte SHA-256** dans le manifeste winget | winget **vérifie** que le fichier téléchargé est exactement le vôtre ; sinon il refuse de l'installer | Gratuit |
| **Signature de code** (Authenticode) | Windows affiche votre nom comme éditeur, supprime l'avertissement SmartScreen, et empêche la modification du binaire après signature | Certificat payant, ou gratuit via des programmes OSS |
| **Code source public** | N'importe qui peut vérifier ce que fait l'application | Gratuit |
| **Builds reproductibles / releases CI** | Prouve que le binaire correspond au code | Gratuit |

**Conclusion** : pour ton cas, la combinaison utile et gratuite est
**HTTPS + SHA-256 + source publique**. C'est exactement ce que fait un manifeste
winget : winget télécharge l'installateur **et vérifie son empreinte** avant de
l'exécuter.

### Et la signature de code ?

C'est la seule protection *réellement* forte (elle empêche qu'on te fasse
installer un faux Ruche). Pistes :

- **SignPath Foundation** — signature **gratuite** pour les projets open source.
- **Azure Trusted Signing** — signature gérée par Microsoft (abonnement modeste,
  identité vérifiée requise).
- Certificat **OV/EV** chez un autorité (Sectigo, DigiCert…) — payant.

Tant que tu n'as pas de certificat, Windows affichera un avertissement
SmartScreen au premier lancement : c'est normal et sans gravité, l'empreinte
SHA-256 publiée dans winget garantit déjà l'intégrité.

---

## 2. Prérequis

- Un **compte GitHub**.
- Un **dépôt public** (ici `ruche`) contenant ce projet.
- Un fichier `LICENSE` (le manifeste le référence).
- L'installateur compilé : `installer\Output\Ruche-Setup-<version>.exe`.

---

## 3. Compiler l'installateur

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\ruche.iss
```

Résultat : `installer\Output\Ruche-Setup-0.1.0.exe`

Pour changer la version ou l'éditeur :

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\ruche.iss `
    /DAppVersion=0.2.0 /DPublisher="Mon Éditeur"
```

---

## 4. Publier l'installateur (GitHub Releases)

1. Créez un dépôt public `ruche` et poussez-y le projet.
2. Créez une **release** avec le tag `v0.1.0`.
3. Joignez-y l'installateur `Ruche-Setup-0.1.0.exe`.

L'URL attendue par winget sera :

```
https://github.com/<compte>/ruche/releases/download/v0.1.0/Ruche-Setup-0.1.0.exe
```

---

## 5. Générer les manifestes winget

```powershell
.\installer\preparer-winget.ps1 -GitHubUser "moncompte"
```

Options utiles :

```powershell
.\installer\preparer-winget.ps1 `
    -GitHubUser "moncompte" `
    -Repo "ruche" `
    -Publisher "Mon Éditeur" `
    -PackageName "Ruche" `
    -Version "0.1.0" `
    -License "MIT"
```

Le script :

- calcule l'empreinte **SHA-256** de l'installateur et l'inscrit dans le manifeste ;
- crée l'arborescence attendue par le dépôt winget :
  `winget\manifests\<initiale>\<Éditeur>\<Paquet>\<Version>\`.

---

## 6. Soumettre à winget

Le dépôt officiel est **`microsoft/winget-pkgs`**. La soumission se fait par
pull request :

1. **Forkez** `microsoft/winget-pkgs`.
2. Copiez le dossier `winget\manifests\<initiale>\...` dans le fork, au même
   emplacement (`manifests\<initiale>\<Éditeur>\<Paquet>\<Version>\`).
3. Commitez et ouvrez une **pull request** vers `microsoft/winget-pkgs:master`.

> Les manifestes doivent être validés automatiquement. S'ils passent, un
> mainteneur fusionne. Un client peut ensuite faire :
>
> ```powershell
> winget install Ruche
> ```
>
> (ou `winget install <Éditeur>.Ruche`)

### Variante automatique

Si vous préférez, l'outil officiel `wingetcreate` fait les trois étapes
(génération + validation + PR) :

```powershell
winget install Microsoft.WingetCreate -e

# Générer depuis l'URL de la release
wingetcreate new "https://github.com/<compte>/ruche/releases/download/v0.1.0/Ruche-Setup-0.1.0.exe"

# Soumettre (nécessite un jeton GitHub avec le droit de fork/PR)
wingetcreate submit --token <TOKEN> .\manifests
```

---

## 7. Mises à jour de version

À chaque nouvelle version :

1. Recompilez avec `/DAppVersion=x.y.z`.
2. Publiez une nouvelle release GitHub avec le tag `vx.y.z` et l'installateur.
3. Régénérez les manifestes : `.\installer\preparer-winget.ps1 -GitHubUser "..." -Version "x.y.z"`.
4. Ouvrez une PR de mise à jour (ou `wingetcreate update`).

---

## 8. Vérifications avant publication

- [ ] `LICENSE` présent à la racine du dépôt.
- [ ] Dépôt **public**.
- [ ] Release **publique** avec l'installateur attaché.
- [ ] Le lien de la release est accessible en HTTPS et le fichier se télécharge.
- [ ] L'empreinte du manifeste correspond bien au fichier publié.
- [ ] L'installateur s'installe **sans droits administrateur** (Ruche est en
      installation par utilisateur : `{localappdata}\Programs\Ruche`).
- [ ] `winget install` puis désinstallation via « Applications » fonctionnent.

---

## 9. Rappel : désinstallation

L'installateur Inno Setup enregistre une entrée dans **Applications et
fonctionnalités** Windows. La désinstallation se fait donc normalement :

```powershell
winget uninstall Ruche
```
