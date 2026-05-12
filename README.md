**Really interesting repo description**

# Installation
This is currently only built for Windows. I believe that indexing multithreading doesn't work properly on other systems, but feel free to test. Searching should work once the index has been made since that's a fairly simple process.

This script (and python in general, in my experience) works better in powershell.

## 1. Python Installation

This requires python 3 or higher. It is recommended to use the latest version for the highest level of compatibility.

## 2. Virtual Environment (optional but strongly recommended)

For optimal system resource isolation, it is strongly recommended to use a virtual environment. While on Windows you can *technically* install packages to your system, it isn't good practice. 

To create a virtual environment open **powershell**, navigate to your desired folder, and use:

```powershell
python3 -m venv venv
```

Before installing packages or running the script you must activate. For Windows, you can do so by running the below command in **powershell**. *This must run every time you want to execute the script and have opened a new console.*

```powershell
./venv/Scripts/activate
```

You will know this has worked if your console looks like:
```powershell
(venv) PS C:\Users\example\code> 
```

## 3. Package Installation
From here, it's easy. You just need to install the required dependencies and everything should run. This is done with:

```powershell
python3 -m pip install -r requirements.txt
```

This should give you a long string of dependencies installing. You don't need to do anything while this works.

# Usage
Once you've installed dependencies, the scripts themselves are pretty easy to use.
## photofind.py
### Index
This is the first command to use. It creates a more readable directory format and also pulls the data from all of your photos. The directory specification will search that directory and everything below it (subfolders and all files). 

You can stop a folder from being indexed (for example, if you're in anti-vehicle tpg and you have a webcam folder) by putting a file named exactly `.noindex` into it. This folder and any within it will not be searched.

Other settings are available such as `--workers` and `--timeout`. These are both optimal given the current implementation and would only need to be changed if you have a weird set of photos. 
- Workers defines the amount of CPU processes to use and is automatically calculated depending on the size of your CPU (it cannot exceed 8 by default).
- Timeout determines the maximum amount of time that can be spent indexing one photo.
  - *The only time I've personally hit the default 5s was when I tried to index a 29 GB photo*.
```powershell
python3 photofind.py index <directory> [--out index.pkl] [--workers 8] [--timeout 5]
```
The default output from this file is `index.pkl`, which is also used by default by the other subcommands. Unless you have multiple directories, I'd recommend keeping it as this.

### Search
Once an index is built, you can search it. This subcommand will take latitude and longitude (formatted 00.00000 or -00.0000) and find the nearest photo to it. If you want more than the first closest picture, you can use the `-n` flag to specify how many results you want, sorted by distance. The index to use and the results file can also be specified, but unless you have multiple indexes, I'd recommend using the defaults.
```powershell
python3 photofind.py search <lat> <lon> [--index index.pkl] [--out results.json] [-n 1]
```
This will return a `results.json` file with the search results as well as a `results.html` which shows the target location and the result photos on an interactive map.

### Combine
This is strictly useful if you have multiple separate directories (ie. if you have multiple drives, two photo folders that aren't in the same root folder, etc.). You can index seperately and then add them together.

```powershell
python3 photofind.py combine <index1.pkl> <index2.pkl> ... [--out merged.pkl]
```
