import os
import requests
import numpy as np
import healpy as hp
from .utils import *
from .file_templates import FileTemplates
import os
from io import StringIO

class DownloadData(): 
    """Download foreground data from Planck Legacy Archive (PLA) and generate CMB realisations."""
    def __init__(self, components: list, 
                 frequencies: list, 
                 realisations: int, 
                 start_realisation: int,
                 directory: str = "data/",
                 overwrite: bool = False): 
        """
        Parameters: 
            components (list): List of components to download. Includes: 'cmb', 'sync', 'dust' (synchrotron)
            frequencies (list): Frequencies of the data to be downloaded.
            realisations (int): Number of realisations to download.
            start_realisation (int): Starting realisation number for processing.
            directory (str): Directory to save the downloaded data.
        """
        self.components = components
        self.frequencies = frequencies
        self.realisations = realisations
        self.start_realisation = start_realisation
        self.directory = directory
        self.overwrite = overwrite

        files = FileTemplates(directory)
        self.download_templates = files.download_templates
        self.file_templates = files.file_templates

    def download_foreground_component(self, component: str, frequency: str, realisation: int = None):
        """
        Downloads the specified foreground component for a given frequency.

        Parameters:
            component (str): The foreground component to download.
            frequency (str): The frequency for which to download the component.
            realisation (int, optional): The realisation number to be downloaded (for noise).
        
        Returns:
            None
        """ 
        template, file_template = self.download_templates[component], self.file_templates[component]
        if realisation is None: 
            # foreground components same across realisations
            filename = file_template.format(frequency=frequency)
        else:
            filename = file_template.format(frequency=frequency, realisation=realisation)
        # Check if the file already exists
        if os.path.exists(filename):
            print(f"File {filename} already exists. Skipping download.")
            return None

        # Format the URL with the current frequency and realisation
        url = template.format(frequency=frequency, realisation=realisation)
        # Send a GET request to the URL
        response = requests.get(url)
        # Check if the request was successful
        if response.status_code == 200:
            # Open the file in binary write mode and write the content
            with open(filename, 'wb') as f:
                f.write(response.content)
            print(f"Downloaded {component} data for frequency {frequency}.")
        else:
            raise ValueError(f"Failed to download {component} data for frequency {frequency}. Status code: {response.status_code}")
        
    def download_cmb_spectrum(self):
        """
        Instead of downloading Planck 2018 best-fit CMB TT spectrum,
        load the theoretical CMB angular power spectrum from a local file.
        """        
        path = os.path.join(self.directory, "cmb_spectrum_theory.txt")
        if os.path.exists(path):
            print("cmb_spectrum_theory.txt exists. Skipping downloaing Planck's spectrum.")
            return
        # download from Planck
        url = "http://pla.esac.esa.int/pla/aio/product-action?COSMOLOGY.FILE_ID=COM_PowerSpect_CMB-TT-full_R3.01.txt"
        print("Downloading Planck TT spectrum...")
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        # Load directly into numpy
        data = np.loadtxt(StringIO(resp.text))
        ells = data[:, 0].astype(int)
        Dl   = data[:, 1]  # μK^2

        # Keep same 4-column format as before
        arr = np.column_stack([ells, Dl, np.zeros_like(ells), np.zeros_like(ells)])
        np.savetxt(path, arr, fmt="%.8e")
        print(f"Wrote Planck cmb_spectrum.txt at {path}") 


    def generate_and_save_cmb_realisation(self, realisation: int, overwrite: bool = False):
        """ Generates a CMB realisation based on the theoretical spectrum and downloads it.
        
        #### Notes: In future, will use CMB data with simulated beams; for now, use theoretical CMB.

        Parameters:
            realisation (int): The realisation number to be generated and downloaded.

        Returns:
            None
        """
        filename = self.file_templates["cmb"].format(realisation=realisation, lmax=1023)  # lmax is set to 1023 for now
        if os.path.exists(filename) and overwrite is False:
            print(f"CMB realisation {realisation} already exists. Skipping generation.")
            return None
        # load the theoretical spectrum, if not exists, load the Planck one
        if os.path.exists(os.path.join(self.directory, "cmb_spectrum_theory.txt")):
            l, dl = np.loadtxt(os.path.join(self.directory, "cmb_spectrum_theory.txt"),
                                comments="#", usecols=(0,1), unpack=True)
            l = l.astype(int)
            dl *= ((2.7255**2) * 1e12) # convert to μK^2
            lmax = 1023
            cl = np.zeros(lmax + 1)
            m = (l >= 2) & (l <= lmax)
            cl[l[m]] = dl[m] * (2.0 * np.pi) / (l[m] * (l[m] + 1.0))   # D_l -> C_l
            nside = 2048
            cmb_map = hp.synfast(cl, nside=nside, pixwin=True, lmax=1023) # convolve with pixel window
            hp.write_map(filename, cmb_map, overwrite=overwrite)
            print(f"Downloaded theoretical CMB.")
        elif os.path.exists(os.path.join(self.directory, "cmb_spectrum.txt")):
            l, dl, _, _ = np.loadtxt(os.path.join(self.directory, "cmb_spectrum.txt")).transpose()
            cl = (dl*2*np.pi)/(l*(l+1))
            cl *= 1E-12 # convert to K 
            nside = 2048
            cmb_map = hp.synfast(cl, nside=nside, pixwin=True, lmax=1023) # convolve with pixel window
            hp.write_map(filename, cmb_map, overwrite=overwrite)
            print(f"Downloaded Planck CMB.")
        else:
            raise FileNotFoundError("No CMB spectrum file found. Please ensure cmb_spectrum_theory.txt or cmb_spectrum.txt exists in the data directory.")



    def download_all(self): 
        """
        Downloads all specified foreground components, noise and CMB realisations.

        Returns:
            None
        """
        # Download foregrounds, which have only one realisation.
        print("Downloading foreground components...")
        for component in self.components:
            if component == "cmb" or component == "noise":
                continue
            else:
                for frequency in self.frequencies:
                    self.download_foreground_component(component, frequency)

        # ensure spectrum exists before generating CMB maps
        self.download_cmb_spectrum()

        # now download CMB and noise, which are realisation dependent.
        for realisation in range(self.realisations):
            realisation += self.start_realisation  # Adjust for starting realisation
            print(realisation)
            print(f"Downloading CMB & noise for realisation {realisation}...")
            self.generate_and_save_cmb_realisation(realisation, self.overwrite)
            if 'noise' in self.components:
                if realisation > 299: 
                    continue # there are only ffp10 300 noise realisations
                else:
                    for frequency in self.frequencies:
                        self.download_foreground_component("noise", frequency, realisation)


# components = ["sync", "dust"]
# frequencies = ["030", "070", "143", "217", "353"]
# realisations = 2
# downloader = DownloadData(components, frequencies, realisations, directory = "/Scratch/matthew/data/", noise=True)
# downloader.download_all()
