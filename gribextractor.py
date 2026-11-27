#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Dec  7 22:01:27 2024

@author: arquey
"""
#from herbie import Herbie
from herbie import HerbieWait
#from herbie import FastHerbie
#import pandas as pd
from datetime import timedelta, datetime, timezone
import os
import shutil
import glob
from flask import Flask, render_template, jsonify
import threading
import configparser
import tempfile
import faulthandler
import signal
from bisect import bisect_left
from typing import Optional
import pygrib
import logging

AUCUN_ECHEC = "Aucune échéance abandonnée"
MAX_TENTATIVES = 4
SOURCES_GFS = {"aws", "google", "azure", "nomads", "ncar_rda"}

TEXT = {
    "fr": {
        "title": "Surveillance du programme GFS NOAA", "loop": "Statut de la boucle",
        "last_file": "Dernier fichier téléchargé", "last_failure": "Dernière échéance abandonnée",
        "clear_failure": "Effacer la dernière échéance abandonnée", "next_file": "Prochain fichier téléchargé",
        "processed": "fichiers traités depuis", "including": "dont", "validated": "téléchargés et validés",
        "startup": "le démarrage", "attempt": "Tentative", "abandoned": "Abandonnés",
        "start": "Démarrer la boucle", "stop": "Arrêter la boucle", "message": "Message",
        "active": "Active", "inactive": "Inactive", "initializing": "Initialisation",
        "no_file": "Aucun fichier téléchargé", "no_failure": AUCUN_ECHEC, "unknown": "Inconnu",
        "running": "Chargement des gribs actif", "restarting": "Erreur de la boucle : redémarrage en attente",
        "starting": "Chargement des gribs en cours de démarrage",
        "stopping": "Chargement des gribs en cours d'arrêt", "stopped": "Chargement des gribs arrêté",
        "interrupted": "Problème : supervision interrompue",
        "already_active": "Le chargement est déjà actif ou en cours d'arrêt",
        "reset_error": "Impossible d'enregistrer la remise à zéro ; consultez les logs.",
    },
    "en": {
        "title": "NOAA GFS monitor", "loop": "Download status",
        "last_file": "Last downloaded file", "last_failure": "Last abandoned forecast hour",
        "clear_failure": "Clear last abandoned forecast hour", "next_file": "Next file to download",
        "processed": "files processed since", "including": "including", "validated": "downloads validated",
        "startup": "startup", "attempt": "Attempt", "abandoned": "Abandoned",
        "start": "Start downloads", "stop": "Stop downloads", "message": "Message",
        "active": "Active", "inactive": "Inactive", "initializing": "Initializing",
        "no_file": "No files downloaded", "no_failure": "No abandoned forecast hours", "unknown": "Unknown",
        "running": "GRIB downloads active", "restarting": "Worker error: waiting to restart",
        "starting": "Starting GRIB downloads", "stopping": "Stopping GRIB downloads",
        "stopped": "GRIB downloads stopped", "interrupted": "Error: supervision stopped",
        "already_active": "Downloads are already active or stopping",
        "reset_error": "Could not save the reset; check the logs.",
    },
}


def display_forecast(value):
    if value == AUCUN_ECHEC:
        return TEXT[UI_LANGUAGE]["no_failure"]
    if UI_LANGUAGE == "en":
        return value.replace("numéro de run=", "run=")
    return value




class gribd:
    def __init__(self,step=1,subsetstring=":[UV]GRD:10 m above|GUST"):
        """
        Classe de gestion des données globales du module gribextractor
        """
        self.runliste= [0,6,12,18]
        self.listefxx = list (range(0, 121,step))+ list(range(123, 385, 3))
        self.lfxx=len(self.listefxx)
        self.boucle_active=False 
        self.thread_boucle: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.verrou = threading.Lock()
        self.verrou_config = threading.RLock()
        self.debut_statistiques = datetime.now(timezone.utc)
        self.compteurs_tentatives = [0] * MAX_TENTATIVES
        self.compteur_abandonnes = 0
        self.pathconfig=os.path.expanduser('~')+'/.config/gribextractor'
        self.dernier_fichier=""
        self.dernier_echec=AUCUN_ECHEC
        self.futur_fichier=""
        self.message="initializing"
        self.subsetstring=subsetstring
        self.step=step
        logger.info(f"gribd initialized with step={step} subsetstring={subsetstring}")  




def config_write (rdate,num,itm):
    srdate=rdate.strftime("%Y/%m/%d")
    tm=GRIBD.listefxx[itm]
    with GRIBD.verrou_config:
        configw = configparser.ConfigParser()
        configw['CURRENT'] = {'daterun': srdate,
                              'numrun': str(num),
                              'TM': str(tm),
                              'checkpoint_version': '2',
                              'dernier_echec': GRIBD.dernier_echec}
        config_save(configw)
    logger.info("Configuration written: next run=%s TM=+%s", rdate, tm)


def config_save(configw):
    path = os.path.join(GRIBD.pathconfig, 'config.txt')
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=GRIBD.pathconfig,
                prefix='.config-', delete=False) as configfile:
            temp_path = configfile.name
            configw.write(configfile)
            configfile.flush()
            os.fsync(configfile.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def config_read ():
    with GRIBD.verrou_config:
        return config_read_locked()


def config_read_locked():
    configr=configparser.ConfigParser()
    configr.read(GRIBD.pathconfig+'/config.txt', encoding='utf-8')
    current = configr['CURRENT']
    srdate = current['daterun']
    inum = int(current['numrun'])
    tm = int(current['TM'])
    if inum not in GRIBD.runliste:
        raise ValueError(f"Invalid run number in configuration: {inum}")
    version = current.get('checkpoint_version')
    if version not in (None, '2'):
        raise ValueError(f"Unknown configuration version: {version}")
    if version is None and GRIBD.step == 3 and tm % 3:
        tm = 3 * (tm // 3)
    itm = bisect_left(GRIBD.listefxx, tm)
    if tm < 0 or itm == GRIBD.lfxx:
        raise ValueError(f"Invalid forecast hour in configuration: {tm}")
    if GRIBD.listefxx[itm] != tm:
        logger.warning("Saved forecast hour +%s unavailable with GRIBSTEP=%s; resuming at +%s",
                       tm, GRIBD.step, GRIBD.listefxx[itm])
    GRIBD.dernier_echec = current.get('dernier_echec', AUCUN_ECHEC)
    rdate=datetime.strptime(srdate, "%Y/%m/%d")
    logger.info(f"Configuration read from {GRIBD.pathconfig+'/config.txt'}")
    if version is None:
        logger.info("Legacy configuration: the last saved forecast hour will be reprocessed")
    return (rdate,inum,itm)
    


def pcata (infile,outfile) :
    with open('outfile', 'ab') as outFile:
        with open('infile', 'rb') as infile :
            shutil.copyfileobj(infile, outFile)

      
        
def make_allgrib (savedir,date,lastrun,lastfxx):
    global nrun 
    logger.info(f"allgrib  {savedir=} {date=} {nrun=} {lastfxx=}")
    sdt=date.strftime("%Y%m%d")
    run_dir="savedir/gfs/"+ sdt +"/" 
    all_file="savedir/gfs/"+ sdt +"/all.grib"
    fxx=0;
    while (fxx <  lastfxx) :
      r="{:03}".format(lastrun)
      f="{:03}".format(fxx)
      path = run_dir+"subset*.t"+r+"*"+f
      for filei in glob.glob(path):
          pcata(filei,all_file)


def chkgribfile (file):
    """Check that the file exists and contains the minimum number of GRIB messages."""
    logging.info(f"file to check: {file}")
    if not (file and os.path.exists(file) and os.path.getsize(file) > 0):
        logging.error(f"invalid file {file}: file does not exist or is empty")
        return False
    
    try:
        grbs = pygrib.open(file)
        try:
            n_values = int(grbs.messages)
        finally:
            grbs.close()
        logging.info(f"{n_values} grib messages found")

        if n_values < GRIB_MIN_MESSAGES:
            logging.error(
                "invalid file %s: found %s GRIB messages, at least %s required",
                file, n_values, GRIB_MIN_MESSAGES)
            return False

        logging.info("File %s is valid (%s GRIB messages)", file, n_values)
        return True
        
    except Exception as e:
        logging.error(f"Error validating file {file}: {e}")
        return False

        
DELAI_NOUVEL_ESSAI = 60
DELAI_SUPERVISION = 30


def prochaine_echeance(run, ifx):
    if ifx + 1 < GRIBD.lfxx:
        return run, ifx + 1
    return run + timedelta(hours=6), 0


def boucle_grib():
    if not os.path.exists(os.path.join(GRIBD.pathconfig, 'config.txt')):
        run = datetime.now(timezone.utc).replace(
            tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        ifx = 0
        config_write(run, run.hour, ifx)
    else:
        date_run, nrun, ifx = config_read()
        run = date_run + timedelta(hours=nrun)

    verbose_flag = logging.getLogger().isEnabledFor(logging.DEBUG)
    while not GRIBD.stop_event.is_set():
        fxx = GRIBD.listefxx[ifx]
        description = f"Daterun={run:%Y/%m/%d} numéro de run={run.hour}Z TM=+{fxx}"
        GRIBD.futur_fichier = description
        delay = (run + timedelta(hours=3) -
                 datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
        if delay > 0:
            logger.info("Waiting before run %s: %.0f seconds", run, delay)
            if GRIBD.stop_event.wait(delay):
                return

        reussi = False
        echecs = 0
        while echecs < MAX_TENTATIVES:
            if GRIBD.stop_event.is_set():
                return
            fichier = None
            try:
                logger.info("Waiting for %s, attempt %s/%s", description.replace("numéro de run=", "run="),
                            echecs + 1, MAX_TENTATIVES)
                try:
                    herbie = HerbieWait(run, model="gfs", product="pgrb2.0p25",
                                        priority=PRIORITY,
                                        wait_for="120s", check_interval="30s",
                                        verbose=verbose_flag, fxx=fxx)
                except TimeoutError:
                    logger.info("File not yet available for %s; continuing to wait",
                                description.replace("numéro de run=", "run="))
                    continue
                if GRIBD.stop_event.is_set():
                    return
                #if herbie.idx is None and herbie.grib_source != "local":
                #    logger.info("Index pas encore disponible pour %s ; nouvelle vérification dans %s secondes",
                #                description, DELAI_NOUVEL_ESSAI)
                #    if GRIBD.stop_event.wait(DELAI_NOUVEL_ESSAI):
                #       return
                    continue
                fichier = herbie.download(GRIBD.subsetstring, verbose=verbose_flag,
                                          errors='raise')
                if chkgribfile(fichier):
                    reussi = True
                    with GRIBD.verrou:
                        GRIBD.dernier_fichier = description
                        GRIBD.compteurs_tentatives[echecs] += 1
                    logger.info("File downloaded and validated: %s", fichier)
                    break
                logger.warning("Invalid GRIB file: %s", fichier)
                if fichier and os.path.exists(fichier):
                    os.remove(fichier)
            except Exception:
                logger.exception("Attempt %s/%s failed for %s",
                                 echecs + 1, MAX_TENTATIVES, description.replace("numéro de run=", "run="))
            echecs += 1
            if GRIBD.stop_event.is_set():
                return
            # attente croissante suivant le nombre d'échecs 1/2/4 minutes 
            if echecs < MAX_TENTATIVES and GRIBD.stop_event.wait(DELAI_NOUVEL_ESSAI*echecs):
                return

        with GRIBD.verrou_config:
            if not reussi:
                GRIBD.dernier_echec = description
                logger.error("Forecast hour abandoned after %s attempts: %s",
                             MAX_TENTATIVES, description.replace("numéro de run=", "run="))
            run, ifx = prochaine_echeance(run, ifx)
            config_write(run, run.hour, ifx)
            if not reussi:
                with GRIBD.verrou:
                    GRIBD.compteur_abandonnes += 1
        


# main loop flask 

app = Flask(__name__)



def superviser_boucle():
    while not GRIBD.stop_event.is_set():
        try:
            GRIBD.message = "running"
            boucle_grib()
            if not GRIBD.stop_event.is_set():
                raise RuntimeError("Worker stopped without a stop request")
        except Exception:
            logger.exception("Worker interrupted; retrying in %s seconds",
                             DELAI_SUPERVISION)
            GRIBD.message = "restarting"
            if GRIBD.stop_event.wait(DELAI_SUPERVISION):
                break
    logger.info("Download worker stopped")


def demarrer_boucle():
    with GRIBD.verrou:
        if GRIBD.boucle_active or (GRIBD.thread_boucle and GRIBD.thread_boucle.is_alive()):
            return False
        GRIBD.stop_event.clear()
        GRIBD.boucle_active = True
        GRIBD.message = "starting"
        GRIBD.thread_boucle = threading.Thread(target=superviser_boucle, daemon=True)
        GRIBD.thread_boucle.start()
        return True


# Configuration du logging
loglevel = os.getenv('LOGLEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, loglevel, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info(f"Configured log level: {loglevel}")

# Réduire les logs des bibliothèques externes
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('flask').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('herbie').setLevel(logging.WARNING)

# Récupération des paramètres depuis les variables d'environnement Docker
def parse_priority(value):
    sources = [source.strip().lower() for source in value.split(",")]
    if any(not source for source in sources):
        raise ValueError("PRIORITY must contain non-empty source names separated by commas")
    invalides = set(sources) - SOURCES_GFS
    if invalides:
        raise ValueError(f"Unknown GFS sources in PRIORITY: {', '.join(sorted(invalides))}")
    return sources


gribstep = int(os.getenv('GRIBSTEP', '1'))
gribsubset = os.getenv('GRIBSUBSET', ":[UV]GRD:10 m above|GUST")
try:
    GRIB_MIN_MESSAGES = int(os.getenv('GRIB_MIN_MESSAGES', '1'))
except ValueError as exc:
    raise ValueError("GRIB_MIN_MESSAGES must be a positive integer") from exc
if GRIB_MIN_MESSAGES < 1:
    raise ValueError("GRIB_MIN_MESSAGES must be a positive integer")
PRIORITY = parse_priority(os.getenv('PRIORITY', 'aws,google,azure'))
UI_LANGUAGE = os.getenv('UI_LANGUAGE', 'fr').strip().lower()
if UI_LANGUAGE not in TEXT:
    raise ValueError("UI_LANGUAGE must be 'fr' or 'en'")
if gribstep <= 0:
    raise ValueError("GRIBSTEP must be a positive integer")

logger.info("Configuration: GRIBSTEP=%s, GRIBSUBSET=%s, GRIB_MIN_MESSAGES=%s, PRIORITY=%s",
            gribstep, gribsubset, GRIB_MIN_MESSAGES, PRIORITY)

GRIBD=gribd(step=gribstep, subsetstring=gribsubset)


@app.route("/")
def index():
    return render_template("index.html", text=TEXT[UI_LANGUAGE], language=UI_LANGUAGE)

@app.route("/demarrer", methods=["POST"])

def demarrer():
    if not demarrer_boucle():
        return jsonify({"status": TEXT[UI_LANGUAGE]["already_active"]}), 409
    return jsonify({"status": TEXT[UI_LANGUAGE][GRIBD.message]})

@app.route("/arreter", methods=["POST"])
def arreter():
    with GRIBD.verrou:
        if GRIBD.boucle_active:
            GRIBD.boucle_active = False
            GRIBD.stop_event.set()
            GRIBD.message = "stopping"
    return jsonify({
        "dernier_fichier": display_forecast(GRIBD.dernier_fichier) if GRIBD.dernier_fichier else TEXT[UI_LANGUAGE]["no_file"],
        "boucle_active": GRIBD.boucle_active,
        "status": TEXT[UI_LANGUAGE][GRIBD.message]
    })


@app.route("/reinitialiser-echec", methods=["POST"])
def reinitialiser_echec():
    try:
        with GRIBD.verrou_config:
            config = configparser.ConfigParser()
            path = os.path.join(GRIBD.pathconfig, 'config.txt')
            with open(path, encoding='utf-8') as configfile:
                config.read_file(configfile)
            config['CURRENT']['dernier_echec'] = AUCUN_ECHEC
            config_save(config)
            GRIBD.dernier_echec = AUCUN_ECHEC
    except (OSError, configparser.Error, KeyError):
        logger.exception("Could not clear the last abandoned forecast hour")
        return jsonify({"erreur": TEXT[UI_LANGUAGE]["reset_error"]}), 500
    logger.info("Last abandoned forecast hour cleared")
    return jsonify({"dernier_echec": display_forecast(GRIBD.dernier_echec)})


@app.route("/statut")
def statut():
    with GRIBD.verrou:
        total = sum(GRIBD.compteurs_tentatives)
        traites = total + GRIBD.compteur_abandonnes
        statistiques = {
            "depuis": GRIBD.debut_statistiques.isoformat(),
            "total": total,
            "traites": traites,
            "abandonnes": {
                "nombre": GRIBD.compteur_abandonnes,
                "pourcentage": round(100 * GRIBD.compteur_abandonnes / traites, 1) if traites else 0.0
            },
            "tentatives": [
                {"tentative": numero, "nombre": nombre,
                 "pourcentage": round(100 * nombre / traites, 1) if traites else 0.0}
                for numero, nombre in enumerate(GRIBD.compteurs_tentatives, 1)
            ]
        }
        thread_actif = bool(GRIBD.thread_boucle and GRIBD.thread_boucle.is_alive())
        if not GRIBD.boucle_active:
            message = TEXT[UI_LANGUAGE]["stopping" if thread_actif else "stopped"]
        elif not thread_actif:
            message = TEXT[UI_LANGUAGE]["interrupted"]
            logger.error("Supervision stopped unexpectedly")
            GRIBD.boucle_active = False
        else:
            message = TEXT[UI_LANGUAGE][GRIBD.message]
        return jsonify({
            "dernier_fichier": display_forecast(GRIBD.dernier_fichier) if GRIBD.dernier_fichier else TEXT[UI_LANGUAGE]["no_file"],
            "dernier_echec": display_forecast(GRIBD.dernier_echec),
            "futur_fichier": display_forecast(GRIBD.futur_fichier) if GRIBD.futur_fichier else TEXT[UI_LANGUAGE]["unknown"],
            "boucle_active": GRIBD.boucle_active,
            "en_arret": not GRIBD.boucle_active and thread_actif,
            "statistiques": statistiques,
            "status": message
        })


def activer_diagnostic_signal():
    if hasattr(signal, "SIGUSR1") and hasattr(faulthandler, "register"):
        faulthandler.register(signal.SIGUSR1, all_threads=True)
        logger.info("Thread diagnostics enabled: SIGUSR1")
    else:
        logger.warning("SIGUSR1 diagnostics unavailable on this platform")


if __name__ == "__main__":
    activer_diagnostic_signal()
    os.makedirs(GRIBD.pathconfig, exist_ok=True)
    demarrer_boucle()
    app.run(debug=False, host="0.0.0.0", port=5959)

