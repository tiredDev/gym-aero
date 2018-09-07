import simulation.quadrotor3 as quad
import simulation.config as cfg
import simulation.animation as ani
import matplotlib.pyplot as pl
import mpl_toolkits.mplot3d.art3d as art3d
import numpy as np
import random
from math import pi, sin, cos
import gym
from gym import error, spaces, utils
from gym.utils import seeding



class LandEnv(gym.Env):
    """
        Environment wrapper for training low-level flying skills. The aircraft is required to land
        at a random positon. It is required to lower onto the landing pad, i.e the angle
        between the aircraft and pad is 90 deg.
    """
    def __init__(self):
        metadata = {'render.modes': ['human']}
        self.goal_thresh = 0.1
        self.t = 0
        self.T = 6
        self.action_space = np.zeros((4,))
        self.observation_space = np.zeros((34,))

        self.goal_xyz = np.array([[0.],
                                [0.],
                                [0.]])
        self.spawn = np.array([[random.uniform(-1.5,1.5)],
                                [random.uniform(-1.5,1.5)],
                                [3.]])
        self.goal_zeta_sin = np.sin(np.array([[0.],
                                            [0.],
                                            [0.]]))
        self.goal_zeta_cos = np.cos(np.array([[0.],
                                            [0.],
                                            [0.]]))

        # we want our aircraft to be flying at zero velocity in the inertial frame
        self.goal_xdot = np.array([[0.],
                                [0.],
                                [-0.5]])
        self.goal_pqr = np.array([[0.],
                                [0.],
                                [0.]])

        # simulation parameters
        self.params = cfg.params
        self.iris = quad.Quadrotor(self.params)
        self.sim_dt = self.params["dt"]
        self.ctrl_dt = 0.05
        self.steps = range(int(self.ctrl_dt/self.sim_dt))
        self.action_bound = [0, self.iris.max_rpm]
        self.H = int(self.T/self.ctrl_dt)
        self.hov_rpm = self.iris.hov_rpm

        self.trim = [self.hov_rpm, self.hov_rpm,self.hov_rpm, self.hov_rpm]
        self.trim_np = np.array(self.trim)
        self.bandwidth = 35.

        self.iris.set_state(self.spawn, np.array([[0.],[0.],[0.]]), np.array([[0.],[0.],[0.]]), np.array([[0.],[0.],[0.]]))
        self.iris.set_rpm(np.array(self.trim))
        xyz, zeta, uvw, pqr = self.iris.get_state()

        self.vec_xyz = xyz-self.goal_xyz
        self.vec_zeta_sin = np.sin(zeta)-self.goal_zeta_sin
        self.vec_zeta_cos = np.cos(zeta)-self.goal_zeta_cos
        self.vec_xdot = self.iris.get_inertial_velocity()-self.goal_xdot
        self.vec_pqr = pqr-self.goal_pqr
        print('NEW!!')

        self.dist_norm = np.linalg.norm(self.vec_xyz)
        self.att_norm_sin = np.linalg.norm(self.vec_zeta_sin)
        self.att_norm_cos = np.linalg.norm(self.vec_zeta_cos)
        self.vel_norm = np.linalg.norm(self.vec_xdot)
        self.ang_norm = np.linalg.norm(self.vec_pqr)

        l, r = self.iris.l, self.iris.prop_radius
        pt = l+r
        p1 = np.array([[0.],[pt],[pt],[0.]])
        p2 = np.array([[0.],[pt],[-pt],[0.]])
        p3 = np.array([[0.],[-pt],[pt],[0.]])
        p4 = np.array([[0.],[-pt],[-pt],[0.]])
        self.P = (p1, p2, p3, p4)

        self.fig = None
        self.axis3d = None
        self.distance_decrease = False

    def reward(self, state, action):
        xyz, zeta, uvw, pqr = state
        x_dot = self.iris.get_inertial_velocity()
        
        # get sin zeta, cos zeta
        s_zeta = np.sin(zeta)
        c_zeta = np.cos(zeta)

        # get vector difference between current states and target states
        curr_dist = xyz-self.goal_xyz
        curr_att_sin = s_zeta-self.goal_zeta_sin
        curr_att_cos = c_zeta-self.goal_zeta_cos
        curr_vel = x_dot-self.goal_xdot
        curr_ang = pqr-self.goal_pqr

        # magnitude of the distance from the goal
        dist_hat = np.linalg.norm(curr_dist)
        att_hat_sin = np.linalg.norm(curr_att_sin)
        att_hat_cos = np.linalg.norm(curr_att_cos)
        vel_hat = np.linalg.norm(curr_vel)
        ang_hat = np.linalg.norm(curr_ang)

        # agent gets a negative reward based on how far away it is from the desired goal state
        dist_rew = 100*(self.dist_norm-dist_hat)
        if dist_rew > 0:
            self.distance_decrease = True
        else:
            self.distance_decrease = False
        att_rew = 10.*((self.att_norm_sin-att_hat_sin)+(self.att_norm_cos-att_hat_cos))
        vel_rew = self.vel_norm-vel_hat
        ang_rew = 5*(self.ang_norm-ang_hat)
        ctrl_rew = -np.sum(((action/self.action_bound[1])**2))
        time_rew = 0.

        self.dist_norm = dist_hat
        self.att_norm_sin = att_hat_sin
        self.att_norm_cos = att_hat_cos
        self.vel_norm = vel_hat
        self.ang_norm = ang_hat
        self.vec_xyz = curr_dist
        self.vec_zeta_sin = curr_att_sin
        self.vec_zeta_cos = curr_att_cos
        self.vec_xdot = curr_vel
        self.vec_pqr = curr_ang
        if self.dist_norm <= self.goal_thresh:
            cmplt_rew = 500.
        elif self.crashed(xyz): 
            cmplt_rew = -500
        elif uvw[2] <= -2.:
            cmplt_rew = -500
        else:
            cmplt_rew = 0.
        return dist_rew, att_rew, vel_rew, ang_rew, ctrl_rew, time_rew, cmplt_rew

    def crashed(self, xyz):
        q = self.iris.state[3:7]
        q_inv = self.iris.q_conj(q)
        Q = self.iris.q_mult(q)
        pts = [Q.dot(self.iris.q_mult(p).dot(q_inv))[1:]+xyz for p in self.P]
        mask = [pt[2] <= 0 for pt in pts]
        if sum(mask) > 0:
            return True
        else: 
            return False

    def terminal(self, pos,):
        xyz, _, uvw, _ = pos
        mask = self.dist_norm > 6
        goal_dist = self.dist_norm

        if np.sum(mask) > 0:
            #print("Out of bounds.")
            return True
        elif uvw[2] <= -2.:
            #print("Vortex ring state.")
            return True
        elif xyz[2] <= 0. or self.crashed(xyz):
            #print("Crashed.")
            return True
        elif goal_dist <=self.goal_thresh:
            #print("Goal Achieved.")
            return True
        elif self.t >= self.T:
            #print("Time limit reached: {:.2f}s".format(self.t))
            return True
        else:
            return False

    def step(self, action):
        """
        Parameters
        ----------
        action :

        Returns
        -------
        ob, reward, episode_over, info : tuple
            ob (object) :
                an environment-specific object representing your observation of
                the environment.
            reward (float) :
                amount of reward achieved by the previous action. The scale
                varies between environments, but the goal is always to increase
                your total reward.
            episode_over (bool) :
                whether it's time to reset the environment again. Most (but not
                all) tasks are divided up into well-defined episodes, and done
                being True indicates the episode has terminated. (For example,
                perhaps the pole tipped too far, or you lost your last life.)
            info (dict) :
                 diagnostic information useful for debugging. It can sometimes
                 be useful for learning (for example, it might contain the raw
                 probabilities behind the environment's last state change).
                 However, official evaluations of your agent are not allowed to
                 use this for learning.
        """
        for _ in self.steps:
            xyz, zeta, uvw, pqr = self.iris.step(self.trim_np+action*self.bandwidth)
        self.t += self.ctrl_dt

        sin_zeta = np.sin(zeta)
        cos_zeta = np.cos(zeta)
        a = (action/self.action_bound[1]).tolist()
        next_state = xyz.T.tolist()[0]+sin_zeta.T.tolist()[0]+cos_zeta.T.tolist()[0]+uvw.T.tolist()[0]+pqr.T.tolist()[0]
        info = self.reward((xyz, zeta, uvw, pqr), action)
        done = self.terminal((xyz, zeta, uvw, pqr))
        reward = sum(info)
        goals = self.vec_xyz.T.tolist()[0]+self.vec_zeta_sin.T.tolist()[0]+self.vec_zeta_cos.T.tolist()[0]+self.vec_xdot.T.tolist()[0]+self.vec_pqr.T.tolist()[0]
        next_state = next_state+a+goals
        return next_state, reward, done, info

    def get_goal(self):
        return self.goal_xyz

    def reset(self):
        self.t = 0.
        self.goal_xyz = np.array([[0.],
                                [0.],
                                [0.]])
        self.spawn = np.array([[random.uniform(-1.5,1.5)],
                                [random.uniform(-1.5,1.5)],
                                [3.]])

        self.iris.set_state(self.spawn, np.arcsin(self.goal_zeta_sin), self.goal_xdot, self.goal_pqr)
        self.iris.set_rpm(np.array(self.trim))
        xyz, zeta, uvw, pqr = self.iris.get_state()
        sin_zeta = np.sin(zeta)
        cos_zeta = np.cos(zeta)
        self.vec_xyz = xyz-self.goal_xyz
        self.vec_zeta_sin = np.sin(zeta)-self.goal_zeta_sin
        self.vec_zeta_cos = np.cos(zeta)-self.goal_zeta_cos
        self.vec_xdot = self.iris.get_inertial_velocity()-self.goal_xdot
        self.vec_pqr = pqr-self.goal_pqr
        a = (self.trim_np/self.action_bound[1]).tolist()
        goals = self.vec_xyz.T.tolist()[0]+self.vec_zeta_sin.T.tolist()[0]+self.vec_zeta_cos.T.tolist()[0]+self.vec_xdot.T.tolist()[0]+self.vec_pqr.T.tolist()[0]
        state = xyz.T.tolist()[0]+sin_zeta.T.tolist()[0]+cos_zeta.T.tolist()[0]+uvw.T.tolist()[0]+pqr.T.tolist()[0]+a+goals
        return state

    def render(self, mode='human', close=False):
        if self.fig is None:
            # rendering parameters
            pl.close("all")
            pl.ion()
            self.fig = pl.figure("Landing")
            self.axis3d = self.fig.add_subplot(111, projection='3d')
            self.vis = ani.Visualization(self.iris, 6, quaternion=True)
        pl.figure("Landing")
        self.axis3d.cla()
        self.vis.draw3d_quat(self.axis3d)
        cir = pl.Circle((self.goal_xyz[0],self.goal_xyz[1]), 0.5)
        self.axis3d.add_patch(cir)
        art3d.pathpatch_2d_to_3d(cir, z=self.goal_xyz[2], zdir="z")
        xyz, _, uvw, _ = self.iris.get_state()
        if self.distance_decrease:
            self.axis3d.plot([self.goal_xyz[0][0],xyz[0][0]],[self.goal_xyz[1][0],xyz[1][0]],[self.goal_xyz[2][0],xyz[2][0]],c='g')
        else:
            self.axis3d.plot([self.goal_xyz[0][0],xyz[0][0]],[self.goal_xyz[1][0],xyz[1][0]],[self.goal_xyz[2][0],xyz[2][0]],c='r')
        self.axis3d.set_xlim(-3, 3)
        self.axis3d.set_ylim(-3, 3)
        self.axis3d.set_zlim(0, 6)
        self.axis3d.set_xlabel('West/East [m]')
        self.axis3d.set_ylabel('South/North [m]')
        self.axis3d.set_zlabel('Down/Up [m]')
        self.axis3d.set_title("Time %.3f s" %(self.t))
        pl.pause(0.001)
        pl.draw()
