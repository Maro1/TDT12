# Copied from: https://github.com/jason9693/midi-neural-processor/blob/bea0dc612b7f687f964d0f6d54d1dbf117ae1307/processor.py

import pretty_midi
import argparse
import os
import pickle
import json
import random

import numpy as np


JSON_FILE = "maestro-v3.0.0.json"

RANGE_NOTE_ON = 128
RANGE_NOTE_OFF = 128
RANGE_VEL = 32
RANGE_TIME_SHIFT = 100


START_IDX = {
    'note_on': 0,
    'note_off': RANGE_NOTE_ON,
    'time_shift': RANGE_NOTE_ON + RANGE_NOTE_OFF,
    'velocity': RANGE_NOTE_ON + RANGE_NOTE_OFF + RANGE_TIME_SHIFT
}


class SustainAdapter:
    def __init__(self, time, type):
        self.start =  time
        self.type = type


class SustainDownManager:
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.managed_notes = []
        self._note_dict = {} # key: pitch, value: note.start

    def add_managed_note(self, note: pretty_midi.Note):
        self.managed_notes.append(note)

    def transposition_notes(self):
        for note in reversed(self.managed_notes):
            try:
                note.end = self._note_dict[note.pitch]
            except KeyError:
                note.end = max(self.end, note.end)
            self._note_dict[note.pitch] = note.start


# Divided note by note_on, note_off
class SplitNote:
    def __init__(self, type, time, value, velocity):
        ## type: note_on, note_off
        self.type = type
        self.time = time
        self.velocity = velocity
        self.value = value

    def __repr__(self):
        return '<[SNote] time: {} type: {}, value: {}, velocity: {}>'\
            .format(self.time, self.type, self.value, self.velocity)


class Event:
    def __init__(self, event_type, value):
        self.type = event_type
        self.value = value

    def __repr__(self):
        return '<Event type: {}, value: {}>'.format(self.type, self.value)

    def to_int(self):
        return START_IDX[self.type] + self.value

    @staticmethod
    def from_int(int_value):
        info = Event._type_check(int_value)
        return Event(info['type'], info['value'])

    @staticmethod
    def _type_check(int_value):
        range_note_on = range(0, RANGE_NOTE_ON)
        range_note_off = range(RANGE_NOTE_ON, RANGE_NOTE_ON+RANGE_NOTE_OFF)
        range_time_shift = range(RANGE_NOTE_ON+RANGE_NOTE_OFF,RANGE_NOTE_ON+RANGE_NOTE_OFF+RANGE_TIME_SHIFT)

        valid_value = int_value

        if int_value in range_note_on:
            return {'type': 'note_on', 'value': valid_value}
        elif int_value in range_note_off:
            valid_value -= RANGE_NOTE_ON
            return {'type': 'note_off', 'value': valid_value}
        elif int_value in range_time_shift:
            valid_value -= (RANGE_NOTE_ON + RANGE_NOTE_OFF)
            return {'type': 'time_shift', 'value': valid_value}
        else:
            valid_value -= (RANGE_NOTE_ON + RANGE_NOTE_OFF + RANGE_TIME_SHIFT)
            return {'type': 'velocity', 'value': valid_value}


def _divide_note(notes):
    result_array = []
    notes.sort(key=lambda x: x.start)

    for note in notes:
        on = SplitNote('note_on', note.start, note.pitch, note.velocity)
        off = SplitNote('note_off', note.end, note.pitch, None)
        result_array += [on, off]
    return result_array


def _merge_note(snote_sequence):
    note_on_dict = {}
    result_array = []

    print('NOTE_SEQ: ', snote_sequence)

    for snote in snote_sequence:
        print('NOTE_DICT: ', note_on_dict)
        if snote.type == 'note_on':
            note_on_dict[snote.value] = snote
        elif snote.type == 'note_off':
            try:
                on = note_on_dict[snote.value]
                print('ON: ', on)
                off = snote
                if off.time - on.time == 0:
                    continue
                print('HERE')
                result = pretty_midi.Note(on.velocity, snote.value, on.time, off.time)
                result_array.append(result)
            except Exception as e:
                print('EX: ', e)
                print('info removed pitch: {}'.format(snote.value))
    return result_array


def _snote2events(snote: SplitNote, prev_vel: int):
    result = []
    if snote.velocity is not None:
        modified_velocity = snote.velocity // 4
        if prev_vel != modified_velocity:
            result.append(Event(event_type='velocity', value=modified_velocity))
    result.append(Event(event_type=snote.type, value=snote.value))
    return result


def _event_seq2snote_seq(event_sequence):
    timeline = 0
    velocity = 0
    snote_seq = []

    for event in event_sequence:
        if event.type == 'time_shift':
            timeline += ((event.value+1) / 100)
        if event.type == 'velocity':
            velocity = event.value * 4
        else:
            snote = SplitNote(event.type, timeline, event.value, velocity)
            snote_seq.append(snote)
    return snote_seq


def _make_time_sift_events(prev_time, post_time):
    time_interval = int(round((post_time - prev_time) * 100))
    results = []
    while time_interval >= RANGE_TIME_SHIFT:
        results.append(Event(event_type='time_shift', value=RANGE_TIME_SHIFT-1))
        time_interval -= RANGE_TIME_SHIFT
    if time_interval == 0:
        return results
    else:
        return results + [Event(event_type='time_shift', value=time_interval-1)]


def _control_preprocess(ctrl_changes):
    sustains = []

    manager = None
    for ctrl in ctrl_changes:
        if ctrl.value >= 64 and manager is None:
            # sustain down
            manager = SustainDownManager(start=ctrl.time, end=None)
        elif ctrl.value < 64 and manager is not None:
            # sustain up
            manager.end = ctrl.time
            sustains.append(manager)
            manager = None
        elif ctrl.value < 64 and len(sustains) > 0:
            sustains[-1].end = ctrl.time
    return sustains


def _note_preprocess(susteins, notes):
    note_stream = []

    for sustain in susteins:
        for note_idx, note in enumerate(notes):
            if note.start < sustain.start:
                note_stream.append(note)
            elif note.start > sustain.end:
                notes = notes[note_idx:]
                sustain.transposition_notes()
            else:
                sustain.add_managed_note(note)

    for sustain in susteins:
        note_stream += sustain.managed_notes

    note_stream.sort(key= lambda x: x.start)
    return note_stream


def encode_midi(file_path):
    events = []
    notes = []
    mid = pretty_midi.PrettyMIDI(midi_file=file_path)

    for inst in mid.instruments:
        inst_notes = inst.notes
        # ctrl.number is the number of sustain control. If you want to know abour the number type of control,
        # see https://www.midi.org/specifications-old/item/table-3-control-change-messages-data-bytes-2
        ctrls = _control_preprocess([ctrl for ctrl in inst.control_changes if ctrl.number == 64])
        notes = inst_notes
        notes.sort(key= lambda x: x.start)

    if len(notes) == 0:
        return []

    dnotes = _divide_note(notes)

    # print(dnotes)
    dnotes.sort(key=lambda x: x.time)
    # print('sorted:')
    # print(dnotes)
    cur_time = 0
    cur_vel = 0
    for snote in dnotes:
        events += _make_time_sift_events(prev_time=cur_time, post_time=snote.time)
        events += _snote2events(snote=snote, prev_vel=cur_vel)
        # events += _make_time_sift_events(prev_time=cur_time, post_time=snote.time)

        cur_time = snote.time
        cur_vel = snote.velocity

    return [e.to_int() for e in events]


def decode_midi(idx_array, file_path=None):
    event_sequence = [Event.from_int(idx) for idx in idx_array]
    snote_seq = _event_seq2snote_seq(event_sequence)
    note_seq = _merge_note(snote_seq)
    note_seq.sort(key=lambda x:x.start)

    mid = pretty_midi.PrettyMIDI()
    # if want to change instument, see https://www.midi.org/specifications/item/gm-level-1-sound-set
    instument = pretty_midi.Instrument(1, False, "Developed By Yang-Kichang")
    instument.notes = note_seq

    mid.instruments.append(instument)
    if file_path is not None:
        mid.write(file_path)
    return mid



# prep_midi
def prep_maestro_midi(maestro_root, output_dir):
    """
    ----------
    Author: Damon Gwinn
    ----------
    Pre-processes the maestro dataset, putting processed midi data (train, eval, test) into the
    given output folder
    ----------
    """

    train_dir = os.path.join(output_dir, "train")
    os.makedirs(train_dir, exist_ok=True)
    val_dir = os.path.join(output_dir, "val")
    os.makedirs(val_dir, exist_ok=True)
    test_dir = os.path.join(output_dir, "test")
    os.makedirs(test_dir, exist_ok=True)

    maestro_json_file = os.path.join(maestro_root, JSON_FILE)
    if(not os.path.isfile(maestro_json_file)):
        print("ERROR: Could not find file:", maestro_json_file)
        return False

    maestro_json = json.load(open(maestro_json_file, "r"))
    print("Found", len(maestro_json['midi_filename']), "pieces")
    print("Preprocessing...")

    total_count = 0
    train_count = 0
    val_count   = 0
    test_count  = 0

    for i in range(len(maestro_json['midi_filename'])):
        mid         = os.path.join(maestro_root, maestro_json["midi_filename"][str(i)])
        split_type  = maestro_json["split"][str(i)]
        f_name      = mid.split("/")[-1] + ".pickle"

        if(split_type == "train"):
            o_file = os.path.join(train_dir, f_name)
            train_count += 1
        elif(split_type == "validation"):
            o_file = os.path.join(val_dir, f_name)
            val_count += 1
        elif(split_type == "test"):
            o_file = os.path.join(test_dir, f_name)
            test_count += 1
        else:
            print("ERROR: Unrecognized split type:", split_type)
            return False

        prepped = encode_midi(mid)

        o_stream = open(o_file, "wb")
        pickle.dump(prepped, o_stream)
        o_stream.close()

        total_count += 1
        if(total_count % 50 == 0):
            print(total_count, "/", len(maestro_json))

    print("Num Train:", train_count)
    print("Num Val:", val_count)
    print("Num Test:", test_count)
    return True

def prep_giant_midi(giant_root, output_dir):
    """
    ----------
    Author: Damon Gwinn
    ----------
    Pre-processes the maestro dataset, putting processed midi data (train, eval, test) into the
    given output folder
    ----------
    """

    train_dir = os.path.join(output_dir, "train")
    os.makedirs(train_dir, exist_ok=True)
    val_dir = os.path.join(output_dir, "val")
    os.makedirs(val_dir, exist_ok=True)
    test_dir = os.path.join(output_dir, "test")
    os.makedirs(test_dir, exist_ok=True)

    files = os.listdir(giant_root)
    random.shuffle(files)
    num_files = len(files)
    train, val, test = np.split(files, [int(len(files)*0.8), int(len(files)*0.9)])
    files = {'train': train, 'val': val, 'test': test}

    print("Found", num_files, "pieces")
    print("Preprocessing...")

    total_count = 0
    train_count = 0
    val_count   = 0
    test_count  = 0

    for split_type in files.keys():
        for filename in files[split_type]:
            mid         = os.path.join(giant_root, filename)
            f_name      = mid.split("/")[-1] + ".pickle"

            if(split_type == "train"):
                o_file = os.path.join(train_dir, f_name)
                train_count += 1
            elif(split_type == "val"):
                o_file = os.path.join(val_dir, f_name)
                val_count += 1
            elif(split_type == "test"):
                o_file = os.path.join(test_dir, f_name)
                test_count += 1
            else:
                print("ERROR: Unrecognized split type:", split_type)
                return False

            try:
                prepped = encode_midi(mid)
            except Exception as e:
                print(e)
                continue

            o_stream = open(o_file, "wb")
            pickle.dump(prepped, o_stream)
            o_stream.close()

            total_count += 1
            if(total_count % 50 == 0):
                print(total_count, "/", num_files)

    print("Num Train:", train_count)
    print("Num Val:", val_count)
    print("Num Test:", test_count)
    return True


# prep_midi
def prep_general_midi(data_root, output_dir):

    train_dir = os.path.join(output_dir, "train")
    os.makedirs(train_dir, exist_ok=True)
    
    print("Preprocessing...")

    total_count = 0
    train_count = 0
    
    mid_list = os.listdir(data_root)

    for piece in mid_list:
        mid         = os.path.join(data_root, piece)
        f_name      = mid.split("/")[-1] + ".pickle"
        print(mid)
        
        o_file = os.path.join(train_dir, f_name)
        train_count += 1

        try:
            prepped = encode_midi(mid)
            if len(prepped) == 0:
                continue
        except Exception as e:
            continue

        o_stream = open(o_file, "wb")
        pickle.dump(prepped, o_stream)
        o_stream.close()

        total_count += 1
        if(total_count % 50 == 0):
            print(total_count, "/", len(mid_list))

    print("Num Train:", train_count)
    return True

    

# parse_args
def parse_args():
    """
    ----------
    Author: Damon Gwinn
    ----------
    Parses arguments for preprocess_midi using argparse
    ----------
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("-dataset", type=int, default=True, help="Which dataset to use. (1: MAESTRO, 2: Giant-MIDI, 3: ADL)")
    parser.add_argument("-root", type=str, default='./data/hymnal', help="Root folder for the Maestro dataset or for custom data.")
    parser.add_argument("-output_dir", type=str, default="./data", help="Output folder to put the preprocessed midi into.")
    
    return parser.parse_args()


def main():
    """
    ----------
    Author: Damon Gwinn
    ----------
    Entry point. Preprocesses maestro and saved midi to specified output folder.
    ----------
    """

    args            = parse_args()
    root            = args.root
    output_dir      = args.output_dir 
    dataset = args.dataset

    print("Preprocessing midi files and saving to", output_dir)
    
    if dataset == 1:
        prep_maestro_midi(root, output_dir) 
    elif dataset == 2:
        prep_giant_midi(root, output_dir) 
    elif dataset == 3:
        prep_giant_midi(root, output_dir)
    else:
        print('ERROR: Unknown dataset')
        return
    print("Done!")
    print("")

if __name__ == "__main__":
    main()
