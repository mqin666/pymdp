import RoboFDM
import numpy as np
import copy
import os
import sys
from .utility import run_cut_process, write_mesh
from .trajectory import TrajStation, Trajectory

from sys import platform
if platform == "linux" or platform == "linux2":
    pass
elif platform == "darwin":
    pass
elif platform == "win32":
    import ctypes
    SEM_NOGPFAULTERRORBOX = 0x8007
    ctypes.windll.kernel32.SetErrorMode(SEM_NOGPFAULTERRORBOX)


class BGS:
    def __init__(self, filename, ranknet=None, export=False):
        self.b_objs = []
        self.b_polys = []
        self.b_poly_sequence = []
        self.b_r_sequence = []
        self.b_rew = []
        self.b_residual = []
        self.b_envs = []
        self.b_round = 0
        self.b_best_so_far = 0
        self.b_trajs = TrajStation()
        self.filename = filename
        self.env = RoboFDM.init()
        self.env.reset(filename)
        self.params = np.array([0.05, 0.55, 0.0, 0.00, 0.25])
        self.threshold = 0.02
        self.n_features = self.env.n_features()
        self.output_folder = None
        self.b_width = None
        self.export_polys = []
        self.export = export

    @staticmethod
    def search_type():
        return "Normal"

    def set_beam_width(self, w):
        self.b_width = w

    def set_output_folder(self, f):
        self.output_folder = f

    def is_diverse(self, a, b):
        dist = abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])
        if dist > 0.95:
            if abs(a[3] - b[3]) < 2.0 * dist:
                return False
        return True

    def r_distance(self, a, b):
        return np.linalg.norm(a-b)

    def query_poly_idx(self, all_range, p):
        sum_range = 0
        for i in range(len(all_range)):
            sum_range += all_range[i]
            if p < sum_range:
                return i
        return 0

    def feedforward_search(self):
        all_r = None
        all_range = []
        self.b_best_so_far = np.max(self.b_rew)
        print('best so far ', self.b_best_so_far)
        print("polys = ", len(self.b_polys))
        print('residual = ', self.b_residual)
        for i in range(len(self.b_polys)):
            self.env.set_poly(self.b_polys[i])
            r = self.env.render()
            r = np.insert(r, 5, 0, axis=1)
            r[:, 5] = r[:, 1]
            #r[:, 1] -=  r[:, 4]
            dr = r[:, 1]
            r[:, 1] += self.b_rew[i]
            r[:, 4] += self.b_residual[i]
            all_range.append(len(dr))
            if all_r is None:
                all_r = r
            else:
                all_r = np.concatenate((all_r, r), axis=0)

        # filter out candidates that not satisfy volume constraint
        violated_vol = (all_r[:, 0] < 0.1)
        all_r[violated_vol, 0:6] = [0, 0, 0, 0, 0, 0]

        cur_sel = []
        cur_polys = []
        cur_r = []
        epsilon = 0.0001
        area_sorted = np.argsort(all_r[:, 1])[::-1]

        poly_sequence = self.b_poly_sequence.copy()
        r_sequence = self.b_r_sequence.copy()
        self.b_poly_sequence.clear()
        self.b_r_sequence.clear()
        self.b_rew.clear()
        self.b_residual.clear()

        # self.b_trajs.display()
        self.b_trajs.move_to_next_level()
        has_impr = False

        '''Construct trajectory features here'''
        traj_feats = []
        cur_traj_node = []
        cur_export_polys = []
        epsilon_best = 0
        while len(cur_sel) < self.b_width:
            for i in range(len(all_r)):
                cur_idx = area_sorted[i]

                if len(cur_sel) >= self.b_width:
                    break
                if all_r[cur_idx, 5] < 1e-4:
                    break
                if all_r[cur_idx, 4] > epsilon:
                    continue
                if all_r[cur_idx, 1] < epsilon_best:
                    break

                poly_idx = self.query_poly_idx(all_range, cur_idx)

                # diversity
                cur_plane = all_r[cur_idx, 6::]
                flag_satisfied = True
                for tmp_r in cur_r:
                    r, pid = tmp_r
                    if pid != poly_idx:  # don't filter if they come from different poly idx
                        continue
                    if self.is_diverse(r[6::], cur_plane) is False:
                        flag_satisfied = False
                        break

                if not flag_satisfied:
                    #print('reject: ', all_r[cur_idx, :])
                    continue

                #print('epsilon = ', all_r[cur_idx, -1], epsilon)

                # print('current feature = ', cur_idx, all_r[cur_idx, 0:5])

                ret_poly = run_cut_process(
                    self.b_polys[poly_idx], cur_plane, self.export)
                if ret_poly == None:
                    print('plane cut failed.')
                    continue

                if type(ret_poly) == tuple:
                    cur_export_polys.append(ret_poly)
                    ret_poly = ret_poly[0]

                cur_sel.append(i)
                new_reward = all_r[cur_idx, 1]
                self.b_rew.append(new_reward)
                self.b_residual.append(all_r[cur_idx, 4])
                if new_reward > self.b_best_so_far:
                    has_impr = True
                #print(all_r[cur_idx, :])
                cur_poly_sequence = poly_sequence[poly_idx]
                cur_poly_sequence.append(ret_poly)
                self.b_poly_sequence.append(cur_poly_sequence)
                cur_r_sequence = r_sequence[poly_idx].copy()
                cur_r_sequence.append(all_r[cur_idx, 1])
                self.b_r_sequence.append(cur_r_sequence)
                cur_polys.append(ret_poly)
                #cur_r.append(np.concatenate((all_r[cur_idx,:], cur_plane), axis=0))
                cur_r.append((all_r[cur_idx, :], poly_idx))
                '''Output mesh'''
                #write_mesh(ret_poly, str(self.b_round) + '-' + str(poly_idx) + '-' + str(len(cur_polys)-1) +'.OFF')

                # create/maintain a trajectory
                traj_feats.append(all_r[cur_idx])
                cur_traj_node.append((poly_idx, len(traj_feats)-1, new_reward))
                #self.b_trajs.add_node(poly_idx, len(traj_feats)-1, new_reward)

            #write_mesh(ret_poly, str(self.b_round) + '-' + str(poly_idx) + '-' + str(itr)+'.OFF')
            # compute diversity function
            # average(L2, pos/weight) < threshold
            epsilon = 5.0 * epsilon
            if len(self.b_rew) != 0:
                epsilon_best = np.max(np.array(self.b_rew))

            if epsilon > 1e3:
                break

        self.export_polys.append(cur_export_polys)
        print('has_import ', has_impr)
        # while loop here
        if has_impr == False:
            return False

        for tn in cur_traj_node:
            (pid, pl, pr) = tn
            self.b_trajs.add_node(pid, pl, pr)

        # print(cur_r)
        feat_valid = len(traj_feats)

        self.b_trajs.add_feature(traj_feats, feat_valid)

        self.b_polys = cur_polys
        # print(self.b_rew)
        return True

    def start_search(self):
        self.b_envs.append(self.env)
        self.b_polys.append(self.env.get_poly())
        self.b_poly_sequence.append(self.b_polys)
        self.b_r_sequence.append([0.0])
        self.b_rew.append(0.0)
        self.b_residual.append(0.0)
        print(self.filename)
        while True:
            pos_rew = self.feedforward_search()
            self.b_round += 1
            if pos_rew == False:
                break

        print(os.path.basename(self.filename)[:-4])
        self.b_trajs.prepare_data(os.path.join(
            self.output_folder, os.path.basename(self.filename)[:-4]))

        self.b_trajs.prepare_data_edge(os.path.join(
            self.output_folder, os.path.basename(self.filename)[:-4]))

        if self.export:
            self.b_trajs.export_best_segmentation(os.path.join(
                self.output_folder, os.path.basename(self.filename)[:-4]), self.export_polys)